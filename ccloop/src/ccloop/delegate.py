"""PreToolUse hook — delegation nudge and long-chain brake.

Why this exists
---------------
Audited over five mxfs Fable loop sessions (721 requests): 65% of requests
were purely mechanical, 50% sat inside chains of >= 3 consecutive mechanical
requests, and the ``Agent`` tool was called **zero** times. Prose alone did
not change that — a written rule telling sessions to delegate had been in
place for a week with no effect. Every behaviour change that ever stuck in
this tree was mechanical, so this is mechanical.

What it does
------------
Tracks, per session, the current streak of consecutive parent ``Bash`` calls
with no ``Read``/``Edit``/``Write``/``Agent`` between them. At
``CCLOOP_DELEGATE_ADVISE`` (default 3) it injects a non-blocking nudge naming
the subagents to hand the rest to. Inside a ccloop run only, at
``CCLOOP_DELEGATE_DENY`` (default 8) it refuses the call outright.

The arithmetic behind those two numbers (docs/context-economics.md, and
scripts/delegate_chain_distribution.py in this repo):

- A denied call has ALREADY cost its request — the turn that emitted it is
  spent. Denying at position N and forcing an ``Agent`` call saves
  ``max(0, L - (N+1))`` on a chain of length L and LOSES ``(N+1) - L`` when
  the chain would have ended on its own.
- Advising costs nothing: ``additionalContext`` rides on a call that runs
  anyway. So advice goes early and refusal goes late.
- The measured chain distribution is fat-tailed — 95 chains, but 6 of length
  11-38 carrying 166 requests. Deny at 8 catches those 6 (the ones that
  actually drain the pool) while risking one request of loss across five
  sessions. Deny at 3 nets more on paper but fires 41 times, i.e. 41 chances
  to thrash.

To bound thrash further, the streak resets after a deny: a chain gets one
refusal, and a session that genuinely needs to continue by hand can.

Contract note
-------------
ccloop's other hooks (``guard``, ``keepgoing``) self-gate on
``CCLOOP_RUN_ID`` and are no-ops in every session that is not a ccloop run.
**This hook deliberately does not.** It advises everywhere, because the burn
it targets is not unique to loop runs; it only *denies* inside a run, where
no human is watching and the tool set is known to include ``Agent``. Set
``CCLOOP_DELEGATE=off`` to disable it entirely.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

# Tools that count as mechanical grind when the PARENT runs them.
GRIND_TOOLS = {"Bash", "BashOutput"}

# Tools that mean real work is happening — they end a grind streak.
RESET_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Agent", "Task"}

# A Bash call that is a blocking WAIT rather than grind. These are neutral:
# they neither advance nor reset the streak.
#
# Waiting is not the behaviour this hook exists to discourage, and punishing it
# is actively counterproductive: a session that correctly delegated and is now
# blocked on the result would be told to "hand this to a subagent" — which it
# already did. Observed live in the mxfs run, where the parent fired a subagent
# and then paid blocking Bash calls polling its `tasks/*.output` file, tripping
# the refusal at 8.
#
# The right fix for that pattern is the instruction (fire it, carry on, and let
# the completion notification wake you) rather than the brake, but a genuinely
# undelegatable wait — a long rig lap that must be watched from the parent —
# still has to be possible without accumulating toward a refusal.
WAIT_PATTERNS = (
    re.compile(r"^\s*until\s"),
    re.compile(r"^\s*while\s+.*;\s*do\b.*\bsleep\b", re.DOTALL),
    re.compile(r"\bsleep\s+\d+.*\bdone\b", re.DOTALL),
    re.compile(r"/tasks/[^\s]*\.output\b"),
)


def is_wait(command):
    """True when a Bash command is a blocking wait rather than mechanical work."""
    if not command:
        return False
    return any(p.search(command) for p in WAIT_PATTERNS)

DEFAULT_ADVISE = 3
DEFAULT_DENY = 8

# State files older than this are pruned on write.
STATE_TTL_SECONDS = 86400

ADVISE = (
    "You are {n} Bash calls into a mechanical chain with no Read/Edit/Agent "
    "between them. Each one costs a full request on the session's own model. "
    "Either batch the rest into a single Bash call, or hand the remainder to "
    "a subagent — {roster} — which runs on a cheaper model and keeps the raw "
    "output out of this context. Delegate chains, not single calls: one Agent "
    "call is itself one request, so it pays only when it replaces several."
)

DENY = (
    "Refused: {n} consecutive Bash calls with no Read/Edit/Agent between them. "
    "This is a long mechanical chain and it is draining the session's request "
    "budget. Hand the rest of it to a subagent — {roster} — with a prompt "
    "specific enough that it can finish without coming back. If the chain "
    "exists only to reach one decisive command, run that command now instead "
    "of the next step. This refusal fires once per chain; the counter has been "
    "reset."
)

ROSTER_FALLBACK = (
    "Agent(subagent_type=\"general-purpose\", model=\"sonnet\")"
)


def _read_stdin_json():
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _int_env(name, default):
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return v if v > 0 else default


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "ccloop" / "delegate"


def _state_path(session_id):
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")
    return state_dir() / ("%s.json" % (safe or "unknown"))


def read_streak(session_id):
    try:
        d = json.loads(_state_path(session_id).read_text(encoding="utf-8"))
        return int(d.get("streak", 0)), int(d.get("last_advised", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0, 0


def write_streak(session_id, streak, last_advised):
    p = _state_path(session_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"streak": streak, "last_advised": last_advised,
                        "updated": int(time.time())}),
            encoding="utf-8",
        )
        os.replace(tmp, p)
    except OSError:
        return
    _prune(p.parent)


def _prune(d):
    cutoff = time.time() - STATE_TTL_SECONDS
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    for f in entries:
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def roster(cwd):
    """Names of subagents available to this project, as a prose list.

    Reads ``<cwd>/.claude/agents/*.md`` and ``~/.claude/agents/*.md``. Falls
    back to the built-in general-purpose agent on sonnet when a project has
    no roster of its own — advising a session to call an agent that does not
    exist is worse than saying nothing.
    """
    names = []
    for d in (Path(cwd or ".") / ".claude" / "agents",
              Path.home() / ".claude" / "agents"):
        try:
            for f in sorted(d.glob("*.md")):
                n = f.stem
                if n not in names:
                    names.append(n)
        except OSError:
            continue
    if not names:
        return ROSTER_FALLBACK
    return "Agent(subagent_type=...): " + ", ".join(names)


def log_event(kind, streak, session_id):
    run_dir = os.environ.get("CCLOOP_RESUME_FILE")
    if not run_dir:
        return
    d = Path(run_dir).parent
    if not d.is_dir():
        return
    try:
        with open(d / "hook-events.log", "a", encoding="utf-8") as fh:
            fh.write("%s\tdelegate-%s\t%s\t%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                kind, streak, session_id or "unknown"))
    except OSError:
        pass


def _emit(decision, reason=None, context=None):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if decision:
        out["hookSpecificOutput"]["permissionDecision"] = decision
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    if context:
        out["hookSpecificOutput"]["additionalContext"] = context
    sys.stdout.write(json.dumps(out) + "\n")


def main(argv=None):
    if os.environ.get("CCLOOP_DELEGATE", "").lower() in ("0", "off", "false"):
        return 0

    data = _read_stdin_json()

    # A subagent's own tool calls are the whole point — never brake them.
    # The CLI's hook schema is explicit that agent_id, NOT agent_type, is the
    # field that distinguishes a subagent call from a main-thread one:
    # agent_type is also present on the main thread of an --agent session.
    if data.get("agent_id"):
        return 0

    session_id = data.get("session_id") or os.environ.get("CCLOOP_SESSION_ID")
    tool = data.get("tool_name") or ""

    streak, last_advised = read_streak(session_id)

    if tool in RESET_TOOLS:
        if streak:
            write_streak(session_id, 0, 0)
        return 0
    if tool not in GRIND_TOOLS:
        return 0
    if is_wait((data.get("tool_input") or {}).get("command", "")):
        # Neutral: a blocking wait is not grind. Leave the streak untouched so
        # waiting neither trips the brake nor launders a real chain.
        return 0

    streak += 1
    advise_at = _int_env("CCLOOP_DELEGATE_ADVISE", DEFAULT_ADVISE)
    deny_at = _int_env("CCLOOP_DELEGATE_DENY", DEFAULT_DENY)
    in_run = bool(os.environ.get("CCLOOP_RUN_ID"))
    names = roster(data.get("cwd"))

    if in_run and deny_at and streak >= deny_at:
        # Reset so one chain earns one refusal, never a refusal loop.
        write_streak(session_id, 0, 0)
        log_event("deny", streak, session_id)
        _emit("deny", reason=DENY.format(n=streak, roster=names))
        return 0

    write_streak(session_id, streak, last_advised)

    # Advise on first crossing, then at most every third call after it, so a
    # long chain is reminded without the nudge becoming noise.
    if streak >= advise_at and (streak == advise_at or streak - last_advised >= 3):
        write_streak(session_id, streak, streak)
        log_event("advise", streak, session_id)
        _emit(None, context=ADVISE.format(n=streak, roster=names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
