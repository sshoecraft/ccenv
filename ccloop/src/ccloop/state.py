"""Project-supplied state hook — the forward-looking half of the handoff.

``summarize.py`` produces the BACKWARD half of a handoff: what the previous
session did. Nothing in it describes what the project looks like *now*, so a
fresh session's only answer to "what should I work on" is "continue whatever
the last session was doing". Projects were expected to close that gap with a
hand-maintained state document, but a document the outgoing model has to
remember to update eventually stops being updated, and a stale one is worse
than none.

So the forward half is generated too: ccloop runs ``<project>/.ccloop/state.sh``
at the start of every session and embeds its stdout in the prompt. The script is
the project's to write (read a defect ledger, print a board tally, whatever);
ccloop only supplies the plumbing. No script means no section and a
byte-identical prompt, so this costs nothing to a project that doesn't opt in.

Run at prompt-build time rather than at summarize time deliberately: prompt
build happens before EVERY session including the first one of a run, and the
output can't go stale because it's computed seconds before the session reads it.
"""

import os
import subprocess
from pathlib import Path

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_BYTES = 8000

HOOK_NAME = "state.sh"
SECTION = "## Current project state"


def project_root(run_dir):
    """The project directory owning ``run_dir``.

    ``run_dir`` is ``<project>/.ccloop/runs/<run-id>`` (see
    ``runner.runs_dir``), so the project is three parents up.
    """
    return Path(run_dir).parent.parent.parent


def hook_path(run_dir):
    """Path to the state hook for this run, honoring ``CCLOOP_STATE_HOOK``."""
    override = os.environ.get("CCLOOP_STATE_HOOK", "").strip()
    if override:
        return Path(override)
    return Path(run_dir).parent.parent / HOOK_NAME


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _wrap(path, body):
    return f"""
---

{SECTION}

Generated at the start of THIS session by the project's state hook:
`{path}`

Everything above describes what PREVIOUS sessions did. This section
describes the project as it is RIGHT NOW, and supersedes anything above
that conflicts with it.

{body}
"""


def _truncate(text, max_bytes):
    """Cut ``text`` to ``max_bytes`` with a VISIBLE marker.

    A silent cap reads to the session as "that is the whole ledger" when it
    isn't, which is exactly the kind of invisible lie this module exists to
    remove — so the truncation names the knob that caused it.
    """
    if max_bytes <= 0 or len(text) <= max_bytes:
        return text
    return (
        text[:max_bytes].rstrip()
        + f"\n\n_(truncated at {max_bytes} bytes — raise "
        "CCLOOP_STATE_HOOK_MAX_BYTES to see the rest)_"
    )


def state_block(run_dir, run_id="", session_num="", log=None):
    """Return the ``## Current project state`` prompt section, or ``""``.

    Never raises: this runs on the path that builds every session's prompt, and
    a project's broken shell script must not be able to stop a run. Failures are
    rendered INTO the block instead of swallowed, so a hook that times out or
    exits nonzero is diagnosable from the session transcript alone; ``log`` (if
    given) additionally gets one line on ccloop's stderr.
    """
    def note(msg):
        if log:
            log(msg)

    path = hook_path(run_dir)
    if not path.is_file():
        return ""

    if not os.access(str(path), os.X_OK):
        note(f"WARNING: state hook {path} is not executable — skipping")
        return _wrap(path, f"_(state hook `{path}` is not executable — `chmod +x` it)_")

    timeout = _env_int("CCLOOP_STATE_HOOK_TIMEOUT", DEFAULT_TIMEOUT)
    max_bytes = _env_int("CCLOOP_STATE_HOOK_MAX_BYTES", DEFAULT_MAX_BYTES)

    root = project_root(run_dir)
    env = dict(os.environ)
    env["CCLOOP_RUN_ID"] = str(run_id)
    env["CCLOOP_RUN_DIR"] = str(run_dir)
    env["CCLOOP_SESSION_NUM"] = str(session_num)
    env["CCLOOP_PROJECT_ROOT"] = str(root)

    try:
        proc = subprocess.run(
            [str(path)],
            cwd=str(root) if Path(root).is_dir() else None,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout if timeout > 0 else None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        note(f"WARNING: state hook {path} timed out after {timeout}s")
        return _wrap(path, f"_(state hook `{path}` timed out after {timeout}s — no state available)_")
    except (OSError, subprocess.SubprocessError) as exc:
        note(f"WARNING: state hook {path} failed to run ({exc})")
        return _wrap(path, f"_(state hook `{path}` failed to run: {exc})_")

    out = _truncate((proc.stdout or "").strip(), max_bytes)

    if proc.returncode != 0:
        err_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
        tail = err_lines[-1].strip() if err_lines else "(no stderr)"
        note(f"WARNING: state hook {path} exited {proc.returncode}: {tail}")
        warning = (
            f"_(state hook `{path}` exited {proc.returncode} — "
            f"treat the above as possibly incomplete. Last stderr line: {tail})_"
        )
        out = f"{out}\n\n{warning}" if out else warning

    if not out:
        note(f"WARNING: state hook {path} produced no output")
        return _wrap(path, f"_(state hook `{path}` produced no output)_")

    return _wrap(path, out)
