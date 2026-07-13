"""The prospect store — state derivation + the full contract lifecycle.

Two state DIMENSIONS, never one overloaded status column (the GPT critique
that reshaped the design):

  attention  — open | fired | acked | deferred | closed
               (what the inbox should do with the item)
  resolution — pending | done | hit | miss | unresolvable | expired
               (what mechanically/judgedly happened to the proposition)

Both are DERIVED by folding ``events.jsonl`` over the immutable contracts;
neither is ever stored as a mutable field.

The load-bearing invariant: closing ATTENTION (cancel_attention, supersede)
never stops RESOLUTION. Cancelled/superseded contracts keep being evaluated
— rate limits still apply — until their original expiry, and a fire after
cancellation records a counterfactual hit. You can stop watching; you cannot
un-make the forecast.

Caps are attention budgets, not quality gates: an active-slot cap and a
daily creation budget (env-tunable). Declining to file is always legal.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from . import contracts as contracts_mod
from . import events as events_mod
from . import paths
from . import predicates
from .contracts import Contract, ContractError
from .predicates import PredicateError
from .util import iso_now, now_utc, parse_iso, to_iso

ACK_DISPOSITIONS = ("done", "keep", "defer", "cancel_attention", "resolve")
RESOLUTIONS = ("hit", "miss", "unresolvable")
BUCKETS = (20, 40, 60, 80)

DEFAULT_MAX_ACTIVE = 20        # env CCPROSPECT_MAX_ACTIVE
DEFAULT_DAILY_BUDGET = 8       # env CCPROSPECT_DAILY_BUDGET
EXPIRING_SOON_HOURS = 72
INTENTION_CAP = 2000
NOTE_CAP = 1000


class StoreError(ValueError):
    """Refusal at the store layer (gate, caps, lifecycle violations)."""


def max_active() -> int:
    try:
        return max(1, int(os.environ.get("CCPROSPECT_MAX_ACTIVE", DEFAULT_MAX_ACTIVE)))
    except ValueError:
        return DEFAULT_MAX_ACTIVE


def daily_budget() -> int:
    try:
        return max(1, int(os.environ.get("CCPROSPECT_DAILY_BUDGET", DEFAULT_DAILY_BUDGET)))
    except ValueError:
        return DEFAULT_DAILY_BUDGET


@dataclass
class ContractState:
    contract: Contract
    attention: str = "open"          # open|fired|acked|deferred|closed
    resolution: str = "pending"      # pending|done|hit|miss|unresolvable|expired
    closed_reason: str | None = None  # done|resolved|cancelled|superseded|expired
    fired_at: str | None = None
    fired_observed: dict | None = None
    counterfactual_fired: bool = False
    first_ack_at: str | None = None
    last_ack_at: str | None = None
    next_review: str | None = None
    successor: str | None = None
    resolved_counterfactually: bool = False
    probe_error: str | None = None
    events: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.contract.id

    @property
    def active(self) -> bool:
        return self.attention in ("open", "fired", "acked", "deferred")

    @property
    def needs_evaluation(self) -> bool:
        # One-shot latching: any fire (live or counterfactual) ends evaluation.
        # Attention-closed items with a pending resolution keep evaluating —
        # that IS the counterfactual invariant.
        return self.resolution == "pending" and self.fired_at is None

    def summary(self) -> dict:
        c = self.contract
        out = {
            "id": c.id,
            "title": c.title,
            "attention": self.attention,
            "resolution": self.resolution,
            "predicate_type": c.predicate.get("type"),
            "expires": c.expires,
            "created_at": c.created_at,
        }
        if self.closed_reason:
            out["closed_reason"] = self.closed_reason
        if self.fired_at:
            out["fired_at"] = self.fired_at
            out["counterfactual_fired"] = self.counterfactual_fired
        if self.next_review:
            out["next_review"] = self.next_review
        if self.successor:
            out["successor"] = self.successor
        if c.bucket is not None:
            out["bucket"] = c.bucket
        return out


def derive_states(all_contracts: dict[str, Contract], all_events: list[dict]) -> dict[str, ContractState]:
    states = {cid: ContractState(contract=c) for cid, c in all_contracts.items()}
    for ev in all_events:
        st = states.get(ev.get("id"))
        if st is None:
            continue
        st.events.append(ev)
        kind = ev.get("event")
        ts = ev.get("ts")

        if kind == "fired":
            if st.fired_at is None:
                st.fired_at = ts
                st.fired_observed = ev.get("observed")
                st.counterfactual_fired = bool(ev.get("counterfactual"))
            if st.attention != "closed":
                st.attention = "fired"
            elif st.resolution == "pending":
                # Fired after cancel/supersede: the looked-for event happened
                # anyway — counterfactual hit, inescapably on the record.
                st.resolution = "hit"
                st.resolved_counterfactually = True

        elif kind == "ack":
            if st.attention == "closed":
                continue
            st.last_ack_at = ts
            if st.first_ack_at is None and st.attention == "fired":
                st.first_ack_at = ts
            disposition = ev.get("disposition")
            if disposition == "done":
                st.attention = "closed"
                st.closed_reason = "done"
                if st.resolution == "pending":
                    st.resolution = "done"
            elif disposition == "keep":
                st.attention = "acked" if st.fired_at else "open"
                st.next_review = None
            elif disposition == "defer":
                st.attention = "deferred"
                st.next_review = ev.get("next_review")
            elif disposition == "cancel_attention":
                st.attention = "closed"
                st.closed_reason = "cancelled"
                if st.fired_at and st.resolution == "pending":
                    # It fired live and was then abandoned — still a hit.
                    st.resolution = "hit"
            elif disposition == "resolve":
                st.attention = "closed"
                st.closed_reason = "resolved"
                if ev.get("resolution") in RESOLUTIONS:
                    st.resolution = ev["resolution"]

        elif kind == "superseded":
            if st.attention != "closed":
                st.attention = "closed"
                st.closed_reason = "superseded"
            st.successor = ev.get("successor")
            if st.fired_at and st.resolution == "pending":
                st.resolution = "hit"

        elif kind == "expired":
            if st.resolution == "pending":
                st.resolution = "expired"
                if ev.get("counterfactual") or st.attention == "closed":
                    st.resolved_counterfactually = True
            if st.attention != "closed":
                st.attention = "closed"
                st.closed_reason = "expired"

    return states


class Store:
    """Facade over one project's ``.ccprospect/`` store."""

    def __init__(self, prospect_dir: Path, create: bool = False):
        self.prospect_dir = Path(prospect_dir)
        self.base_dir = self.prospect_dir.parent
        if create:
            paths.contracts_dir(self.prospect_dir).mkdir(parents=True, exist_ok=True)
        if self.prospect_dir.exists():
            paths.ensure_gitignore(self.prospect_dir)

    # -- state ---------------------------------------------------------------

    def states(self) -> dict[str, ContractState]:
        return derive_states(contracts_mod.load_all(self.prospect_dir),
                             events_mod.read_events(self.prospect_dir))

    def resolve_id(self, fragment: str) -> str:
        return contracts_mod.resolve_id(contracts_mod.load_all(self.prospect_dir), fragment)

    # -- probe-state (LOCAL, per-machine) --------------------------------------

    def _load_probe_state(self) -> dict:
        p = paths.probe_state_path(self.prospect_dir)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_probe_state(self, state: dict):
        p = paths.probe_state_path(self.prospect_dir)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(state, indent=1), encoding="utf-8")
        except OSError:
            pass  # local cache only — never fatal

    # -- creation --------------------------------------------------------------

    def create(self, *, title: str, intention: str, predicate: dict, expires: str,
               expect: str | None = None, bucket: int | None = None,
               evidence: str | None = None, predecessor: str | None = None,
               session: str | None = None, via_amend: bool = False) -> Contract:
        now = now_utc()

        title = (title or "").strip()
        if not title:
            raise StoreError("title is required")
        if len(title) > contracts_mod.TITLE_CAP:
            raise StoreError(f"title exceeds {contracts_mod.TITLE_CAP} chars — keep it index-line short")
        intention = (intention or "").strip()
        if not intention:
            raise StoreError("intention is required — what should the waking session DO when this fires?")
        if len(intention) > INTENTION_CAP:
            raise StoreError(f"intention exceeds {INTENTION_CAP} chars")

        try:
            expires_dt = parse_iso(expires)
        except ValueError as e:
            raise StoreError(f"unparseable 'expires': {e}")
        if expires_dt <= now:
            raise StoreError("'expires' must be in the future — nothing is open-ended, and nothing is born dead")

        if bucket is not None:
            if not expect:
                raise StoreError("'bucket' is only meaningful with an 'expect' claim — add expect or drop bucket")
            if int(bucket) not in BUCKETS:
                raise StoreError(f"'bucket' must be one of {BUCKETS} (no 50 — it becomes a default escape hatch)")
            bucket = int(bucket)

        states = self.states()

        if not via_amend:
            fired_unacked = [st.id for st in states.values() if st.attention == "fired"]
            if fired_unacked:
                raise StoreError(
                    "creation refused: fired prospect(s) awaiting acknowledgment: "
                    + ", ".join(sorted(fired_unacked))
                    + " — call prospect_inbox() and prospect_ack(...) each before filing new ones")

        active = sum(1 for st in states.values() if st.active)
        cap = max_active()
        if active >= cap and not via_amend:
            raise StoreError(
                f"creation refused: {active} active prospects at the cap of {cap} "
                "(attention budget, not a quality gate) — resolve, cancel, or amend an existing one first")

        today = now.strftime("%Y-%m-%d")
        created_today = sum(
            1 for ev in events_mod.read_events(self.prospect_dir)
            if ev.get("event") == "created" and str(ev.get("ts", "")).startswith(today))
        budget = daily_budget()
        if created_today >= budget:
            raise StoreError(
                f"creation refused: daily creation budget reached ({created_today}/{budget} today) — "
                "an attention budget; file it tomorrow or raise CCPROSPECT_DAILY_BUDGET")

        clean = predicates.validate(predicate)
        clean = predicates.creation_check(clean, base_dir=self.base_dir, now=now)

        paths.contracts_dir(self.prospect_dir).mkdir(parents=True, exist_ok=True)
        paths.ensure_gitignore(self.prospect_dir)

        session = session or os.environ.get("CLAUDE_SESSION_ID") or None
        for attempt in range(5):
            cid = contracts_mod.next_id(self.prospect_dir)
            fields = {
                "id": cid, "title": title, "intention": intention,
                "predicate": clean, "expires": to_iso(expires_dt),
                "expect": (expect or "").strip() or None,
                "bucket": bucket,
                "evidence": (evidence or "").strip() or None,
                "predecessor": predecessor,
                "created_at": to_iso(now), "session": session,
            }
            try:
                contract = contracts_mod.write_contract(self.prospect_dir, fields)
                break
            except FileExistsError:
                continue  # concurrent id allocation — rescan and retry
        else:
            raise StoreError("could not allocate a contract id after 5 attempts")

        events_mod.append_event(self.prospect_dir,
                                {"event": "created", "id": contract.id, "ts": to_iso(now)})
        if clean["type"] in predicates.CMD_TYPES:
            # The creation probe just ran; start the rate-limit clock now.
            ps = self._load_probe_state()
            ps.setdefault("last_probe", {})[contract.id] = now.timestamp()
            self._save_probe_state(ps)
        return contract

    # -- evaluation (the wake boundary) ----------------------------------------

    def evaluate(self, *, at_session_start: bool = False,
                 allow_probes: bool = True) -> dict:
        """Evaluate every pending predicate; append fired/expired events.

        Serial, rate-limited, one-shot. Returns a summary of what changed.
        Cancelled/superseded-but-unresolved contracts are evaluated too —
        counterfactual resolution is not optional.
        """
        now = now_utc()
        states = self.states()
        ps = self._load_probe_state()
        last_probe = ps.setdefault("last_probe", {})
        fired: list[dict] = []
        expired: list[str] = []
        probes_run = 0

        for cid in sorted(states):
            st = states[cid]
            if not st.needs_evaluation:
                continue
            c = st.contract
            ptype = c.predicate.get("type")
            is_cmd = ptype in predicates.CMD_TYPES

            try:
                past_expiry = now >= parse_iso(c.expires)
            except ValueError:
                past_expiry = False  # unparseable expiry: keep evaluating, never auto-expire

            if past_expiry:
                # One FINAL evaluation (ignoring min_interval) so a cmd/path
                # contract gets its last chance to resolve before expiring.
                if is_cmd and not allow_probes:
                    events_mod.append_event(self.prospect_dir, {
                        "event": "expired", "id": cid, "ts": to_iso(now),
                        "counterfactual": st.attention == "closed",
                        "probe_skipped": True,
                    })
                    expired.append(cid)
                    continue
                did_fire, observed, ran = predicates.evaluate(
                    c.predicate, base_dir=self.base_dir, now=now,
                    created_at=c.created_at, at_session_start=at_session_start)
                probes_run += 1 if ran else 0
                if ran:
                    last_probe[cid] = now.timestamp()
                if did_fire:
                    ev = events_mod.append_event(self.prospect_dir, {
                        "event": "fired", "id": cid, "ts": to_iso(now),
                        "observed": observed,
                        "counterfactual": st.attention == "closed",
                    })
                    fired.append({"id": cid, "title": c.title, "observed": observed,
                                  "counterfactual": st.attention == "closed",
                                  "fired_at": ev["ts"]})
                else:
                    events_mod.append_event(self.prospect_dir, {
                        "event": "expired", "id": cid, "ts": to_iso(now),
                        "counterfactual": st.attention == "closed",
                    })
                    expired.append(cid)
                continue

            if is_cmd:
                if not allow_probes:
                    continue
                interval = int(c.predicate.get("min_interval", predicates.DEFAULT_MIN_INTERVAL))
                last = float(last_probe.get(cid, 0))
                if now.timestamp() - last < interval:
                    continue

            did_fire, observed, ran = predicates.evaluate(
                c.predicate, base_dir=self.base_dir, now=now,
                created_at=c.created_at, at_session_start=at_session_start)
            if ran:
                probes_run += 1
                last_probe[cid] = now.timestamp()
            if did_fire:
                ev = events_mod.append_event(self.prospect_dir, {
                    "event": "fired", "id": cid, "ts": to_iso(now),
                    "observed": observed,
                    "counterfactual": st.attention == "closed",
                })
                fired.append({"id": cid, "title": c.title, "observed": observed,
                              "counterfactual": st.attention == "closed",
                              "fired_at": ev["ts"]})

        self._save_probe_state(ps)
        return {"fired": fired, "expired": expired, "probes_run": probes_run,
                "evaluated_at": to_iso(now)}

    # -- inbox -----------------------------------------------------------------

    def inbox(self, *, evaluate_first: bool = True, at_session_start: bool = False,
              allow_probes: bool = True) -> dict:
        if evaluate_first:
            self.evaluate(at_session_start=at_session_start, allow_probes=allow_probes)
        now = now_utc()
        states = self.states()

        fired_rows, due_rows, expiring_rows = [], [], []
        for st in sorted(states.values(), key=lambda s: s.id):
            c = st.contract
            if st.attention == "fired":
                fired_rows.append({
                    "id": c.id, "title": c.title, "intention": c.intention,
                    "fired_at": st.fired_at, "observed": st.fired_observed,
                    "predicate_type": c.predicate.get("type"),
                    "expires": c.expires, "expect": c.expect, "bucket": c.bucket,
                })
            elif st.attention == "deferred" and st.next_review:
                try:
                    due = parse_iso(st.next_review) <= now
                except ValueError:
                    due = True
                if due:
                    due_rows.append({
                        "id": c.id, "title": c.title, "intention": c.intention,
                        "next_review": st.next_review, "expires": c.expires,
                    })
            if st.active:
                try:
                    dt = parse_iso(c.expires)
                except ValueError:
                    continue
                if now < dt <= now + timedelta(hours=EXPIRING_SOON_HOURS):
                    expiring_rows.append({"id": c.id, "title": c.title, "expires": c.expires})

        active = sum(1 for st in states.values() if st.active)
        return {
            "fired": fired_rows,
            "due": due_rows,
            "expiring_soon": expiring_rows,
            "pending_count": len(fired_rows) + len(due_rows),
            "active": active,
            "cap": max_active(),
        }

    # -- dispositions ------------------------------------------------------------

    def ack(self, id_fragment: str, disposition: str, *, resolution: str | None = None,
            note: str | None = None, evidence: str | None = None,
            next_review: str | None = None) -> dict:
        if disposition not in ACK_DISPOSITIONS:
            raise StoreError(f"disposition must be one of {ACK_DISPOSITIONS}")
        cid = self.resolve_id(id_fragment)
        st = self.states()[cid]
        if st.attention == "closed":
            raise StoreError(
                f"{cid} attention is already closed ({st.closed_reason}) — outcomes are immutable; "
                "file a new prospect if the intention lives on")

        ev: dict = {"event": "ack", "id": cid, "disposition": disposition}
        if disposition == "resolve":
            if resolution not in RESOLUTIONS:
                raise StoreError(f"'resolve' requires resolution ∈ {RESOLUTIONS}")
            ev["resolution"] = resolution
        if disposition == "defer":
            if not next_review:
                raise StoreError("'defer' requires next_review (ISO-8601) — a deferral without a date is abandonment")
            try:
                ev["next_review"] = to_iso(parse_iso(next_review))
            except ValueError as e:
                raise StoreError(f"unparseable next_review: {e}")
        if disposition == "cancel_attention" and not (note or "").strip():
            raise StoreError("'cancel_attention' requires a note (the reason) — no silent abandonment")
        if note:
            ev["note"] = str(note)[:NOTE_CAP]
        if evidence:
            ev["evidence"] = str(evidence)[:NOTE_CAP]

        events_mod.append_event(self.prospect_dir, ev)
        return self.states()[cid].summary()

    def amend(self, id_fragment: str, **overrides) -> dict:
        """Supersede: new immutable successor; the original keeps resolving
        counterfactually to its own terms until its own expiry."""
        cid = self.resolve_id(id_fragment)
        st = self.states()[cid]
        if st.attention == "closed":
            raise StoreError(
                f"{cid} is closed ({st.closed_reason}) — nothing to amend; file a new prospect")
        old = st.contract

        fields = {
            "title": old.title, "intention": old.intention,
            "predicate": {k: v for k, v in old.predicate.items() if k != "baseline"},
            "expires": old.expires, "expect": old.expect, "bucket": old.bucket,
            "evidence": old.evidence,
        }
        for key in ("title", "intention", "predicate", "expires", "expect", "bucket", "evidence"):
            if overrides.get(key) is not None:
                fields[key] = overrides[key]

        successor = self.create(via_amend=True, predecessor=cid,
                                session=overrides.get("session"), **fields)
        events_mod.append_event(self.prospect_dir, {
            "event": "superseded", "id": cid, "successor": successor.id,
        })
        return {"superseded": cid, "successor": successor.id,
                "note": f"{cid} still resolves counterfactually at its original expiry ({old.expires})"}

    # -- queries -----------------------------------------------------------------

    def list_all(self, status: str | None = None) -> list[dict]:
        states = self.states()
        rows = []
        for st in sorted(states.values(), key=lambda s: s.id):
            if status and status != "all":
                if status == "active":
                    if not st.active:
                        continue
                elif status == "closed":
                    if st.attention != "closed":
                        continue
                elif st.attention != status:
                    continue
            rows.append(st.summary())
        return rows

    def get(self, id_fragment: str) -> dict:
        cid = self.resolve_id(id_fragment)
        st = self.states()[cid]
        c = st.contract
        out = st.summary()
        out.update({
            "intention": c.intention,
            "predicate": c.predicate,
            "expect": c.expect,
            "evidence": c.evidence,
            "predecessor": c.predecessor,
            "session": c.session,
            "path": str(c.path) if c.path else None,
            "events": st.events,
        })
        return out

    # -- the factual report --------------------------------------------------------

    def report(self) -> dict:
        """Aging + calibration, factual only: counts, denominators, latencies.
        No adjectives, no thresholds, no recommendations — presentation-induced
        policy is the failure mode this format exists to avoid."""
        now = now_utc()
        states = list(self.states().values())

        by_attention: dict[str, int] = {}
        by_resolution: dict[str, int] = {}
        for st in states:
            by_attention[st.attention] = by_attention.get(st.attention, 0) + 1
            by_resolution[st.resolution] = by_resolution.get(st.resolution, 0) + 1

        ack_latencies = []
        for st in states:
            if st.fired_at and st.first_ack_at and not st.counterfactual_fired:
                try:
                    delta = parse_iso(st.first_ack_at) - parse_iso(st.fired_at)
                    ack_latencies.append(delta.total_seconds() / 3600.0)
                except ValueError:
                    pass

        calibration: dict[str, dict] = {}
        for st in states:
            if st.contract.bucket is None:
                continue
            b = str(st.contract.bucket)
            row = calibration.setdefault(b, {"n": 0, "hit": 0, "miss": 0,
                                             "expired": 0, "unresolvable": 0, "pending": 0})
            row["n"] += 1
            key = st.resolution if st.resolution in ("hit", "miss", "expired", "unresolvable") else "pending"
            if st.resolution == "done":
                key = "hit"  # a completed intention with a bucketed expect counts as hit
            row[key] += 1

        def counterfactuals(reason: str) -> dict:
            group = [st for st in states if st.closed_reason == reason]
            return {
                "n": len(group),
                "counterfactual_hit": sum(1 for st in group if st.resolution == "hit"),
                "counterfactual_expired": sum(1 for st in group if st.resolution == "expired"),
                "pending": sum(1 for st in group if st.resolution == "pending"),
            }

        def ages(pred) -> dict:
            vals = []
            for st in states:
                if pred(st):
                    try:
                        vals.append((now - parse_iso(st.contract.created_at)).total_seconds() / 86400.0)
                    except ValueError:
                        pass
            return {"n": len(vals), "median_days": round(median(vals), 2) if vals else None}

        return {
            "total": len(states),
            "by_attention": by_attention,
            "by_resolution": by_resolution,
            "active": sum(1 for st in states if st.active),
            "cap": max_active(),
            "daily_budget": daily_budget(),
            "fired_awaiting_ack": sum(1 for st in states if st.attention == "fired"),
            "ack_latency_hours": {
                "n": len(ack_latencies),
                "median": round(median(ack_latencies), 2) if ack_latencies else None,
            },
            "calibration_by_bucket": calibration,
            "cancelled": counterfactuals("cancelled"),
            "superseded": counterfactuals("superseded"),
            "age_days": {
                "open": ages(lambda st: st.attention == "open"),
                "active": ages(lambda st: st.active),
            },
            "generated_at": iso_now(),
        }
