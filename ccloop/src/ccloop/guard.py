"""PostToolUse hook — context guard.

Fires after every tool call inside a ccloop run. If the session's current
token usage is at or above the run's cutoff (``<run-dir>/cutoff``), it
injects a friendly wrap-up suggestion via ``additionalContext`` so the
next assistant turn sees it. It is a no-op outside a ccloop run
(``CCLOOP_RUN_ID`` unset).

Token usage is read exclusively from the ccusage statusline cache
(``$TMPDIR/ccusage-<uid>.json``). That cache carries the exact
``used_percentage`` and ``context_window_size`` Claude Code itself
computes, scaled to a token count. It is only trusted when its
``session_id`` matches this session — a concurrent Claude Code session's
cache write is ignored, otherwise the guard would fire on the wrong run.

There is intentionally no transcript-based fallback. The transcript's
per-turn API token counts (input + cache_creation + cache_read) do not
match Claude Code's live context indicator on Opus 4.8 / 1 M-context
sessions with heavy system prompts: the first turn's raw input alone
routinely exceeds the cutoff. Trusting the cache and skipping the check
when it's unavailable is safer than firing on inflated transcript
numbers.
"""

import json
import os
import sys
import time
from pathlib import Path

from . import usage


DEFAULT_CUTOFF_TOKENS = 250000

# A cutoff smaller than this is almost certainly a typo (someone wrote
# "160" expecting "thousands of tokens" — the file stores raw tokens).
# The smallest cutoff the CLI can produce is 1000 (--cutoff=1), so any
# legitimate value is ≥ 1000. Below that, fall back to the default.
MIN_REASONABLE_CUTOFF_TOKENS = 1000


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


def _read_cutoff(run_dir):
    """Read ``<run-dir>/cutoff`` as an int token count.

    Returns the default when the file is absent, unparsable, or a value
    smaller than ``MIN_REASONABLE_CUTOFF_TOKENS`` (a hand-edited "160"
    expecting thousands would otherwise fire the guard on every tool
    call).
    """
    if run_dir is None:
        return DEFAULT_CUTOFF_TOKENS
    p = Path(run_dir) / "cutoff"
    try:
        value = int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return DEFAULT_CUTOFF_TOKENS
    if value < MIN_REASONABLE_CUTOFF_TOKENS:
        return DEFAULT_CUTOFF_TOKENS
    return value


WRAP_UP = (
    "Heads up — you've reached the ccloop relay boundary. Good stopping "
    "point: please wrap up the current sub-step, then end with a brief "
    "text summary. Remaining work will continue in a fresh session with "
    "full transcript access — no need to write any handoff document, the "
    "loop wrapper produces it from your transcript automatically."
)


def main(argv=None):
    if not os.environ.get("CCLOOP_RUN_ID"):
        return 0

    _read_stdin_json()  # drain; we don't need anything from it

    resume_file = os.environ.get("CCLOOP_RESUME_FILE")
    run_dir = Path(resume_file).parent if resume_file else None
    cutoff = _read_cutoff(run_dir)
    if cutoff <= 0:
        return 0

    tokens = usage.exact_tokens(os.environ.get("CCLOOP_SESSION_ID"))
    if tokens is None or tokens < cutoff:
        return 0

    if run_dir is not None and run_dir.is_dir():
        try:
            with open(run_dir / "hook-events.log", "a", encoding="utf-8") as fh:
                fh.write(
                    "%s\tfired\t%s\t%s\n"
                    % (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        tokens,
                        os.environ.get("CCLOOP_SESSION_ID", "unknown"),
                    )
                )
        except OSError:
            pass

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": WRAP_UP,
        }
    }
    sys.stdout.write(json.dumps(out) + "\n")
    return 0
