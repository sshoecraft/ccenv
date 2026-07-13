"""Typed predicates v1 — deliberately tiny (the trading lesson transplanted).

| type          | fires when                                            |
|---------------|-------------------------------------------------------|
| at            | now >= time                                           |
| session_start | next SessionStart evaluation in this repo             |
| path_exists   | path appears (negate: disappears)                     |
| path_changed  | content hash differs from creation-stamped baseline   |
| cmd_ok        | probe exits 0                                         |
| cmd_fail      | probe exits nonzero                                   |
| cmd_match     | probe stdout matches regex                            |

Everything else (git state, URL state, CI status, an API level) is
expressible as a ``cmd_*`` probe — the universal event source for dev
environments. NO compound predicates in v1 ("do not add compound predicates
until the simple system has produced enough failure data").

Creation-time rules (all mechanical):
  - the predicate must be well-formed AND not already true (probes run once
    at creation; ``path_changed`` stamps its baseline hash) — a prospect
    that is born fired is retroactive success, refused;
  - probes are the one security/latency surface: shell commands authored by
    the agent, so timeout is hard-capped at 10s, output capped, evaluation
    is strictly serial, ``min_interval`` rate-limits re-probing, and
    ``CCPROSPECT_NO_PROBES=1`` disables probe evaluation at SessionStart.

Relative paths resolve against the STARTUP dir (the ``.ccprospect`` parent),
and probes run with it as cwd, so contracts stay portable across clones.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .util import parse_iso, to_iso

PREDICATE_TYPES = ("at", "session_start", "path_exists", "path_changed",
                   "cmd_ok", "cmd_fail", "cmd_match")
CMD_TYPES = ("cmd_ok", "cmd_fail", "cmd_match")

PROBE_TIMEOUT_MAX = 10          # seconds, hard cap
PROBE_TIMEOUT_DEFAULT = 10
PROBE_OUTPUT_CAP = 4096         # bytes of probe output retained for matching
OBSERVED_EXCERPT_CAP = 400      # chars of output stored in a fired event
DEFAULT_MIN_INTERVAL = 3600     # seconds between probes of one contract


class PredicateError(ValueError):
    """Refusal: malformed predicate, already-true at creation, or bad field."""


def validate(predicate: dict) -> dict:
    """Normalize + validate shape. Returns a clean copy; raises PredicateError."""
    if not isinstance(predicate, dict):
        raise PredicateError("predicate must be an object with a 'type' field")
    ptype = predicate.get("type")
    if ptype not in PREDICATE_TYPES:
        raise PredicateError(
            f"unknown predicate type {ptype!r} — v1 types: {', '.join(PREDICATE_TYPES)}")

    clean: dict = {"type": ptype}

    if ptype == "at":
        if not predicate.get("time"):
            raise PredicateError("predicate 'at' requires a 'time' (ISO-8601)")
        try:
            clean["time"] = to_iso(parse_iso(predicate["time"]))
        except ValueError as e:
            raise PredicateError(f"unparseable 'time': {e}")

    elif ptype == "session_start":
        pass

    elif ptype in ("path_exists", "path_changed"):
        path = predicate.get("path")
        if not path or not str(path).strip():
            raise PredicateError(f"predicate '{ptype}' requires a 'path'")
        clean["path"] = str(path).strip()
        if ptype == "path_exists":
            clean["negate"] = bool(predicate.get("negate", False))
        if ptype == "path_changed" and predicate.get("baseline"):
            clean["baseline"] = str(predicate["baseline"])

    else:  # cmd_*
        run = predicate.get("run")
        if not run or not str(run).strip():
            raise PredicateError(f"predicate '{ptype}' requires a 'run' shell command")
        clean["run"] = str(run).strip()
        timeout = predicate.get("timeout", PROBE_TIMEOUT_DEFAULT)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            raise PredicateError("'timeout' must be an integer number of seconds")
        if not 1 <= timeout <= PROBE_TIMEOUT_MAX:
            raise PredicateError(f"'timeout' must be 1..{PROBE_TIMEOUT_MAX} seconds")
        clean["timeout"] = timeout
        min_interval = predicate.get("min_interval", DEFAULT_MIN_INTERVAL)
        try:
            min_interval = int(min_interval)
        except (TypeError, ValueError):
            raise PredicateError("'min_interval' must be an integer number of seconds")
        if min_interval < 0:
            raise PredicateError("'min_interval' must be >= 0")
        clean["min_interval"] = min_interval
        if ptype == "cmd_match":
            regex = predicate.get("regex")
            if not regex:
                raise PredicateError("predicate 'cmd_match' requires a 'regex'")
            try:
                re.compile(regex)
            except re.error as e:
                raise PredicateError(f"invalid regex: {e}")
            clean["regex"] = str(regex)

    return clean


def run_probe(run: str, timeout: int, base_dir: Path) -> dict:
    """Run one shell probe. Returns {exit, output, error} — exit is None on
    timeout or spawn failure (recorded, never raises)."""
    try:
        proc = subprocess.run(
            run, shell=True, cwd=str(base_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        output = proc.stdout.decode("utf-8", errors="replace")[:PROBE_OUTPUT_CAP]
        return {"exit": proc.returncode, "output": output, "error": None}
    except subprocess.TimeoutExpired:
        return {"exit": None, "output": "", "error": f"probe timed out after {timeout}s"}
    except OSError as e:
        return {"exit": None, "output": "", "error": f"probe failed to spawn: {e}"}


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _resolve_path(raw: str, base_dir: Path) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(base_dir) / p
    return p


def creation_check(predicate: dict, *, base_dir: Path, now: datetime) -> dict:
    """Refuse a predicate that is already true; stamp mechanical baselines.

    Returns the predicate dict (possibly extended, e.g. path_changed's
    ``baseline``). Probes run exactly once here.
    """
    ptype = predicate["type"]

    if ptype == "at":
        if parse_iso(predicate["time"]) <= now:
            raise PredicateError(
                f"'at' time {predicate['time']} is not in the future — already true")

    elif ptype == "session_start":
        pass  # designed to fire at the next wake; cannot be pre-true

    elif ptype == "path_exists":
        p = _resolve_path(predicate["path"], base_dir)
        negate = predicate.get("negate", False)
        exists = p.exists()
        if exists and not negate:
            raise PredicateError(f"path already exists: {p} — predicate already true")
        if not exists and negate:
            raise PredicateError(f"path already absent: {p} — predicate already true")

    elif ptype == "path_changed":
        p = _resolve_path(predicate["path"], base_dir)
        baseline = _hash_file(p)
        if baseline is None:
            raise PredicateError(
                f"cannot baseline 'path_changed': {p} is missing or unreadable")
        predicate = dict(predicate)
        predicate["baseline"] = baseline

    else:  # cmd_*
        probe = run_probe(predicate["run"], predicate["timeout"], base_dir)
        if probe["error"]:
            raise PredicateError(
                f"creation probe could not establish a baseline ({probe['error']}) — "
                "fix the command or raise its timeout (max "
                f"{PROBE_TIMEOUT_MAX}s)")
        if ptype == "cmd_ok" and probe["exit"] == 0:
            raise PredicateError("probe already exits 0 — predicate already true")
        if ptype == "cmd_fail" and probe["exit"] != 0:
            raise PredicateError(
                f"probe already exits nonzero ({probe['exit']}) — predicate already true")
        if ptype == "cmd_match" and re.search(predicate["regex"], probe["output"]):
            raise PredicateError("probe output already matches regex — predicate already true")

    return predicate


def evaluate(predicate: dict, *, base_dir: Path, now: datetime,
             created_at: str, at_session_start: bool) -> tuple[bool, dict | None, bool]:
    """Evaluate one predicate. Returns (fired, observed, probe_ran).

    ``observed`` is mechanically populated — these values land in the fired
    event so the model cannot fake them. Rate limiting and the NO_PROBES
    switch are the caller's job (store.evaluate); this function is stateless.
    """
    ptype = predicate["type"]

    if ptype == "at":
        target = parse_iso(predicate["time"])
        if now >= target:
            return True, {"time": predicate["time"], "now": to_iso(now)}, False
        return False, None, False

    if ptype == "session_start":
        # Fires only in a SessionStart-hook evaluation strictly after the
        # creating session's moment — "remind me next time I wake here."
        # prospect_inbox() mid-session must not fire it. The 1s margin
        # covers second-truncated ISO timestamps; a real relay/restart is
        # always slower than that.
        if at_session_start:
            try:
                created = parse_iso(created_at)
            except ValueError:
                created = None
            if created is None or (now - created) >= timedelta(seconds=1):
                return True, {"session_start": to_iso(now)}, False
        return False, None, False

    if ptype == "path_exists":
        p = _resolve_path(predicate["path"], base_dir)
        exists = p.exists()
        fired = (not exists) if predicate.get("negate") else exists
        if fired:
            return True, {"path": str(p), "exists": exists}, False
        return False, None, False

    if ptype == "path_changed":
        p = _resolve_path(predicate["path"], base_dir)
        current = _hash_file(p)
        baseline = predicate.get("baseline")
        if current != baseline:
            return True, {"path": str(p), "baseline": baseline,
                          "current": current or "missing"}, False
        return False, None, False

    # cmd_* — a probe actually runs
    probe = run_probe(predicate["run"], predicate.get("timeout", PROBE_TIMEOUT_DEFAULT), base_dir)
    excerpt = probe["output"][:OBSERVED_EXCERPT_CAP]
    if probe["error"]:
        # A timeout / spawn failure is not a firing — not even for cmd_fail,
        # which is about the command's own exit status.
        return False, {"probe_error": probe["error"]}, True

    if ptype == "cmd_ok":
        if probe["exit"] == 0:
            return True, {"exit": 0, "excerpt": excerpt}, True
        return False, None, True
    if ptype == "cmd_fail":
        if probe["exit"] != 0:
            return True, {"exit": probe["exit"], "excerpt": excerpt}, True
        return False, None, True
    # cmd_match
    m = re.search(predicate["regex"], probe["output"])
    if m:
        return True, {"exit": probe["exit"], "matched": m.group(0)[:200],
                      "excerpt": excerpt}, True
    return False, None, True
