"""Hook handlers — entry points invoked by Claude Code via settings.json.

All handlers are fail-open: any error → exit 0 → allow the operation.
Prospective memory is a quality-of-life layer; it must never block real work
(the guard hook's deny on the generated/immutable files is the one
deliberate exception — that IS its job).

- ``session`` : SessionStart — evaluate every open predicate (the
                "remembering to remember" moment), then inject the inbox
- ``stop``    : regenerate PROSPECT.md
- ``guard``   : block Write/Edit on contracts/, events.jsonl, PROSPECT.md
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import index_gen
from . import paths
from .store import Store


def _read_stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


SESSION_PROTOCOL = """\
## ccprospect protocol (prospective memory — the FUTURE store)

`.ccprospect/` holds intentions/forecasts prior sessions filed against future
events. Each has an immutable contract (typed predicate + expiry + what to DO
when it fires) and an append-only outcome record.

- FIRED or DUE items above are mail from a prior session to THIS one. For each,
  read its intention and submit exactly one disposition:
  `prospect_ack(id, disposition)` — `done` (intention carried out) | `keep`
  (noted, keep active) | `defer` (+next_review) | `cancel_attention` (+note,
  required) | `resolve` (+resolution hit|miss|unresolvable, for forecasts).
  `prospect_file` REFUSES new prospects while fired items sit unacknowledged.
- To leave your own future self a cue with teeth: `prospect_file(title,
  intention, predicate, expires[, expect, bucket, evidence])`. Predicates:
  at / session_start / path_exists / path_changed / cmd_ok / cmd_fail /
  cmd_match. Declining to file is always fine — never file filler.
- Contracts are immutable. To revise one: `prospect_amend(id, ...)` creates a
  successor; the original still resolves counterfactually at its own expiry.
- `prospect_inbox()` re-evaluates on demand; `prospect_report()` gives the
  factual aging/calibration record."""


def session_handler() -> int:
    """SessionStart: evaluate all open predicates, inject the inbox.

    Only fires when this project has a ``.ccprospect/`` store — a project
    opts in by filing its first prospect; everywhere else this is silent.
    cmd probes run here unless CCPROSPECT_NO_PROBES=1 (per-item min_interval
    rate limits always apply).
    """
    _read_stdin_json()
    d = paths.resolve_prospect_dir()
    if not d:
        return 0

    allow_probes = not os.environ.get("CCPROSPECT_NO_PROBES")
    store = Store(d)
    inbox = store.inbox(evaluate_first=True, at_session_start=True,
                        allow_probes=allow_probes)

    lines = [
        f"PROSPECT INBOX: {len(inbox['fired'])} fired, {len(inbox['due'])} due review, "
        f"{len(inbox['expiring_soon'])} expiring soon — {inbox['active']} active "
        f"(cap {inbox['cap']})."
    ]
    for row in inbox["fired"]:
        lines.append(f"  🔥 [{row['id']}] {row['title']} — fired {row['fired_at']} "
                     f"({row['predicate_type']}); intention: {row['intention']}")
    for row in inbox["due"]:
        lines.append(f"  ⏰ [{row['id']}] {row['title']} — review was due {row['next_review']}; "
                     f"intention: {row['intention']}")
    for row in inbox["expiring_soon"]:
        lines.append(f"  ⌛ [{row['id']}] {row['title']} — expires {row['expires']}")

    lines.append(_aging_nudge(store, inbox))
    context = "\n".join(l for l in lines if l) + "\n\n" + SESSION_PROTOCOL

    try:
        index_gen.write(d)
    except Exception:
        pass  # digest regen is best-effort here; the Stop hook retries

    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    print(json.dumps(out))
    return 0


def _aging_nudge(store: Store, inbox: dict) -> str:
    """Escalate when fired items sit unacknowledged across session starts
    (same data-driven pattern as ccmemory's compile nudge). Counter lives in
    the LOCAL probe_state.json — per machine, like the probe watermarks."""
    try:
        ps = store._load_probe_state()
        seen = ps.setdefault("fired_seen_sessions", {})
        fired_ids = {row["id"] for row in inbox["fired"]}
        for cid in list(seen):
            if cid not in fired_ids:
                del seen[cid]
        aged = []
        for cid in sorted(fired_ids):
            seen[cid] = int(seen.get(cid, 0)) + 1
            if seen[cid] >= 2:
                aged.append(f"{cid} ({seen[cid]} sessions)")
        store._save_probe_state(ps)
        if aged:
            return ("  ⚠ fired and STILL unacknowledged across multiple sessions: "
                    + ", ".join(aged))
        return ""
    except Exception:
        return ""


def stop_handler() -> int:
    _read_stdin_json()  # drain
    d = paths.resolve_prospect_dir()
    if not d:
        return 0
    try:
        result = index_gen.write(d)
        sys.stderr.write(f"[ccprospect] PROSPECT.md regen: {result['bytes']}B\n")
    except Exception as e:
        sys.stderr.write(f"[ccprospect] regen failed (fail-open): {e}\n")
    return 0


GUARDED_HINT = (
    "This file is part of ccprospect's record and is not hand-editable:\n"
    "- contracts/ files are IMMUTABLE — to revise one, prospect_amend(id, ...) "
    "creates a successor (the original still resolves counterfactually);\n"
    "- events.jsonl is append-only and tool-authored — dispositions go through "
    "prospect_ack(id, disposition, ...);\n"
    "- PROSPECT.md is GENERATED — it is regenerated on Stop, so edits get "
    "clobbered.\n"
    "File new prospects with prospect_file(...)."
)


def guard_handler() -> int:
    """Deny Write/Edit on the immutable/generated parts of .ccprospect/."""
    payload = _read_stdin_json()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name not in ("Write", "Edit", "NotebookEdit"):
        return 0
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path:
        return 0

    marker = f"/{paths.PROJECT_LOCAL_DIRNAME}/"
    if marker not in path:
        return 0
    tail = path.split(marker, 1)[1]
    guarded = (
        tail.startswith(paths.CONTRACTS_DIRNAME + "/")
        or tail == paths.EVENTS_FILENAME
        or tail == paths.INDEX_FILENAME
    )
    if guarded:
        print(json.dumps({"permissionDecision": "deny", "reason": GUARDED_HINT}))
        return 2
    return 0


HANDLERS = {
    "session": session_handler,
    "stop": stop_handler,
    "guard": guard_handler,
}


def dispatch(name: str) -> int:
    handler = HANDLERS.get(name)
    if not handler:
        sys.stderr.write(f"[ccprospect] unknown hook: {name}\n")
        return 0  # fail-open
    try:
        return handler()
    except Exception as e:
        sys.stderr.write(f"[ccprospect] hook {name} crashed (fail-open): {e}\n")
        return 0
