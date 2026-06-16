"""Stop hook — keep the session going.

Modern Claude models often "stop" mid-task with no real reason — they
emit a final text turn and sit idle waiting for input. The Stop hook
fires the instant a turn ends, so it is the precise point at which we can
intervene: if the task isn't actually complete, return a JSON object that
**blocks the stop and re-feeds a continue message**, and the model keeps
working.

Convergence has two modes:

1. **No criteria** (legacy path). The model signals "actually done" by
   writing ``DONE`` to ``$CCLOOP_RESUME_FILE``. Hook trusts that.

2. **Criteria configured** (``<run-dir>/criteria.md`` exists and is
   non-empty). The DONE marker is ignored entirely. The hook re-feeds
   the criteria verbatim and asks the model the direct yes/no question:
   have you met them? If yes, write YES to ``<run-dir>/criteria-met``.
   The hook accepts the stop only when that marker file exists with YES
   as its first token. The criteria text being in the model's face at
   the moment of decision is the whole point — the model can no longer
   stop by writing DONE as a reflex; it has to confront the bar.

Self-gates:
- ``CCLOOP_RUN_ID`` unset → no-op (the hook is registered globally; it
  must do nothing in non-ccloop sessions).
- Session id from stdin must match ``CCLOOP_SESSION_ID`` if both present
  → never blocks a foreign session's stop in a concurrent ccloop scenario.

Safety cap: ``CCLOOP_MAX_CONTINUES`` (default 0 = unlimited) bounds the
number of times this hook will re-feed within a single session, so a
model that genuinely cannot make progress eventually gets to exit. The
counter is kept under the run dir (``<run-dir>/keepgoing-<sess>.count``).
"""

import glob
import json
import os
import sys
import time
from pathlib import Path

from . import usage


DEFAULT_CUTOFF_TOKENS = 250000
MIN_REASONABLE_CUTOFF_TOKENS = 1000


CONTINUE_MSG = (
    "Continue the task. You stopped without signaling completion. The "
    "task is only complete when you have verifiably finished what was "
    "asked AND you have run:\n\n"
    "    echo DONE > \"$CCLOOP_RESUME_FILE\"\n\n"
    "Until then, keep working. Do not ask clarifying questions or wait "
    "for input — make a reasonable choice and proceed. If you are truly "
    "blocked on something you cannot resolve yourself, document the "
    "blocker in a brief text turn, write DONE to the resume file, and "
    "stop; the next loop iteration (or the user) will pick it up."
)


CRITERIA_MSG_TEMPLATE = (
    "HAVE YOU MET THE CRITERIA?\n\n"
    "<BEGIN CRITERIA>\n"
    "{criteria}\n"
    "<END CRITERIA>\n\n"
    "IF YES (every criterion verifiably met): write YES to the marker "
    "file and stop:\n\n"
    "    echo YES > \"{marker}\"\n\n"
    "IF NO: keep working.\n\n"
    "*** YOU HAVE UNLIMITED CONTEXT AND UNLIMITED SESSIONS *** — when "
    "your context fills, ccloop will summarize your transcript and "
    "continue in a fresh session against these same criteria. The "
    "criteria are your ONLY exit condition.\n\n"
    "Do not stop. Do not ask the user what to do. Do not propose ending "
    "the session, killing the wrapper, or claiming a fresh session is "
    "needed — the wrapper handles all of that automatically. Do not "
    "write YES dishonestly.\n\n"
    "Pick a new angle (re-read code, write targeted diagnostics, form "
    "a new hypothesis, consult any external analysis tools available) "
    "and keep working."
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


def _first_token(text):
    if not text or not text.strip():
        return ""
    return text.lstrip().split()[0]


def _is_done_legacy(resume_file):
    """Legacy DONE-in-resume-file check (used when no criteria configured)."""
    if not resume_file:
        return False
    p = Path(resume_file)
    if not p.exists():
        return True
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not text.strip():
        return True
    return _first_token(text).upper().rstrip(":")[:4] == "DONE"


def _run_dir(resume_file):
    if not resume_file:
        return None
    d = Path(resume_file).parent
    return d if d.is_dir() else None


def _criteria_text(run_dir):
    """Non-empty contents of <run-dir>/criteria.md, or None."""
    if run_dir is None:
        return None
    p = run_dir / "criteria.md"
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None  # empty criteria.md = explicit opt-out
    return text.strip()


def _criteria_met(run_dir):
    """True if <run-dir>/criteria-met exists with YES as its first token."""
    if run_dir is None:
        return False
    p = run_dir / "criteria-met"
    if not p.is_file():
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _first_token(text).upper().rstrip(":") == "YES"


def _bump_counter(run_dir, session_id):
    if run_dir is None or not session_id:
        return 0
    counter = run_dir / f"keepgoing-{session_id}.count"
    try:
        n = int(counter.read_text().strip()) if counter.exists() else 0
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        counter.write_text(str(n))
    except OSError:
        pass
    return n


def _read_cutoff(run_dir):
    # A hand-edited 0 (or any non-positive value) is the explicit "no
    # cutoff" sentinel — returned as-is so main()'s ``if cutoff > 0`` gate
    # is skipped and the run keeps going until the session window fills.
    if run_dir is None:
        return DEFAULT_CUTOFF_TOKENS
    p = run_dir / "cutoff"
    try:
        value = int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return DEFAULT_CUTOFF_TOKENS
    if value <= 0:
        return 0
    if value < MIN_REASONABLE_CUTOFF_TOKENS:
        return DEFAULT_CUTOFF_TOKENS
    return value


def _signal_halt(run_dir, session_id, tokens, cutoff):
    """Write the halt sentinel and append a hook-events.log entry.

    The sentinel ``<run-dir>/halt-<session_id>`` is what the interactive
    watcher in ``runner.run_session_interactive`` polls for; once it
    appears, the TUI is terminated so ``loop()`` can relay. Headless mode
    needs no sentinel — ``-p`` exits on its own when this hook allows the
    stop.
    """
    if run_dir is None or not session_id:
        return
    try:
        (run_dir / f"halt-{session_id}").write_text("", encoding="utf-8")
    except OSError:
        pass
    try:
        with open(run_dir / "hook-events.log", "a", encoding="utf-8") as fh:
            fh.write(
                "%s\thalt\t%s\t%s\t%s\n"
                % (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    tokens,
                    cutoff,
                    session_id,
                )
            )
    except OSError:
        pass


def _emit_block(reason, n):
    sys.stdout.write(json.dumps({
        "decision": "block",
        "reason": reason,
        "systemMessage": f"ccloop keepgoing — continue until done (re-fed #{n})",
    }) + "\n")


def _emit_wait(n):
    """Block the stop because background work is pending — without the
    keepgoing nudge.

    ``return 0`` would NOT work here: ccloop is actively driving the session
    (relay on session-end), so allowing the stop loses the running task.
    We must emit ``decision: block`` to keep the session alive. The
    ``reason`` is intentionally minimal — the model is already correctly
    waiting; any prose from us pushes toward fresh action, which is wrong.

    A wait re-feed is bounded by external work, not model pathology, so it
    intentionally does NOT bump the keepgoing counter and is not capped by
    ``CCLOOP_MAX_CONTINUES``.
    """
    sys.stdout.write(json.dumps({
        "decision": "block",
        "reason": "Wait. Background command still running.",
        "systemMessage": f"ccloop wait — {n} background command(s) still running",
    }) + "\n")


PROC_ROOT = "/proc"

# Non-procfs (e.g. macOS) fallback: an .output file modified within this
# many seconds is treated as a still-running task. Bounds the false positive
# to this window instead of forever; a procfs-equipped host never uses it.
STALE_OUTPUT_SECONDS = 90


def _outputs_with_live_writer(output_paths):
    """Subset of ``output_paths`` currently held open by a live process.

    A still-running Bash background command keeps its ``.output`` open for
    writing; the harness does NOT reap the file when the command finishes —
    it lingers on disk for the rest of the session (and beyond). So file
    *presence* is not *liveness*. On Linux we read liveness straight from
    procfs: which of these paths is the target of some ``/proc/<pid>/fd/<n>``
    symlink. No subprocess, no lsof — just readlink, short-circuited once
    every path is accounted for, and only on Stop.

    Returns the set of live paths, or ``None`` when procfs is unavailable
    (the caller then falls back to an mtime window).
    """
    targets = set(output_paths)
    try:
        pids = os.listdir(PROC_ROOT)
    except OSError:
        return None  # no procfs on this platform
    live = set()
    for pid in pids:
        if not pid.isdigit():
            continue
        fd_dir = os.path.join(PROC_ROOT, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue  # process exited mid-scan, or not ours to read
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target in targets:
                live.add(target)
        if len(live) == len(targets):
            break
    return live


def _recently_modified(output_paths, now):
    """Subset of ``output_paths`` modified within ``STALE_OUTPUT_SECONDS``.

    The non-procfs liveness approximation: a genuinely-running task writes
    (or at least was created) recently, while a leftover .output from a
    long-finished task is stale and must not keep the gate firing forever.
    """
    fresh = set()
    for p in output_paths:
        try:
            mtime = os.stat(p).st_mtime
        except OSError:
            continue
        if now - mtime <= STALE_OUTPUT_SECONDS:
            fresh.add(p)
    return fresh


def _pending_background_task_count(session_id):
    """Return how many of this session's ``*.output`` files belong to a
    background command that is STILL RUNNING.

    Claude Code stores per-session Bash-background output under
    ``/tmp/claude-<uid>/<slug>/<session-id>/tasks/<task-id>.output`` and does
    NOT delete the file when the command completes — it persists for the life
    of the session. Counting bare presence therefore wedges ccloop: once any
    background command has ever run, its orphaned .output makes this gate
    re-fire on every subsequent Stop forever, so the session can never relay
    or exit (it just emits "N background command(s) still running" until the
    context wall). The fix is a liveness check — only an .output held open by
    a live process counts (procfs), with an mtime window as the non-procfs
    fallback.

    Returns 0 on any error or when the dir can't be located.
    """
    if not session_id:
        return 0
    pattern = f"/tmp/claude-{os.getuid()}/*/{session_id}/tasks"
    matches = glob.glob(pattern)
    if len(matches) != 1:
        return 0
    tasks_dir = matches[0]
    try:
        outputs = [
            os.path.join(tasks_dir, name)
            for name in os.listdir(tasks_dir)
            if name.endswith(".output")
        ]
    except OSError:
        return 0
    if not outputs:
        return 0
    live = _outputs_with_live_writer(outputs)
    if live is None:
        live = _recently_modified(outputs, time.time())
    return len(live)


def main(argv=None):
    if not os.environ.get("CCLOOP_RUN_ID"):
        return 0

    hook_input = _read_stdin_json()
    own_sid = os.environ.get("CCLOOP_SESSION_ID")
    hook_sid = hook_input.get("session_id")
    if own_sid and hook_sid and own_sid != hook_sid:
        return 0

    resume_file = os.environ.get("CCLOOP_RESUME_FILE")
    run_dir = _run_dir(resume_file)
    criteria = _criteria_text(run_dir)

    try:
        cap = int(os.environ.get("CCLOOP_MAX_CONTINUES") or 0)
    except ValueError:
        cap = 0

    # Legitimate completion always wins over the cutoff: a real DONE / YES
    # should end the run cleanly, not trigger a relay to a fresh session.
    if criteria is None:
        if _is_done_legacy(resume_file):
            return 0
    else:
        if _criteria_met(run_dir):
            return 0

    # Cutoff gate. If this session has crossed the token cutoff, allow the
    # stop (no re-feed, no counter bump) so the loop can summarize and
    # relay. Write the halt sentinel so the interactive watcher SIGTERMs
    # the TUI; headless -p exits on its own.
    #
    # Token usage comes solely from the ccusage cache scoped to this
    # session_id. There is no transcript fallback: the per-turn API counts
    # over-estimate the live context window on Opus 4.8 / 1 M sessions and
    # would halt spuriously at the start of every session. If the cache is
    # absent or belongs to a concurrent session, we let the model keep
    # working.
    #
    # MUST run BEFORE the background-work gate: at the cutoff we relay
    # regardless of pending tasks (losing a task is recoverable; blowing
    # past the context wall is not).
    cutoff = _read_cutoff(run_dir)
    if cutoff > 0:
        tokens = usage.exact_tokens(own_sid)
        if tokens is not None and tokens >= cutoff:
            _signal_halt(run_dir, own_sid, tokens, cutoff)
            return 0

    # Background-work gate. If the model has a Bash task pending in this
    # session's tasks dir, block the stop with a minimal-reason re-feed
    # instead of the keepgoing nudge — the model is correctly waiting for
    # an async result, and pushing it to "pick a new angle" is wrong. We
    # cannot `return 0` here: ccloop's runner relays on session-end, so
    # allowing the stop loses the running task. Block (re-feed) to keep
    # the session alive. Counter intentionally NOT bumped — wait cycles
    # are bounded by external work, not the model-pathology cap protects.
    n_pending = _pending_background_task_count(own_sid)
    if n_pending:
        _emit_wait(n_pending)
        return 0

    if criteria is None:
        n = _bump_counter(run_dir, own_sid)
        if cap > 0 and n > cap:
            return 0
        _emit_block(CONTINUE_MSG, n)
        return 0

    n = _bump_counter(run_dir, own_sid)
    if cap > 0 and n > cap:
        # Safety net so a stuck run can eventually escape.
        return 0

    marker = str(run_dir / "criteria-met")
    _emit_block(CRITERIA_MSG_TEMPLATE.format(criteria=criteria, marker=marker), n)
    return 0
