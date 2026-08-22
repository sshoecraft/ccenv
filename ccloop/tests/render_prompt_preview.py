#!/usr/bin/env python3
"""Print the exact prompt ccloop hands a session, for eyeballing wording.

The pytest suite asserts the structure; this renders it so a human can read
the actual text — particularly the state-hook block, whose phrasing is what
steers the session.

    python3 tests/render_prompt_preview.py                  # legacy preamble
    python3 tests/render_prompt_preview.py --criteria "..." # criteria preamble
    python3 tests/render_prompt_preview.py --hook ./my.sh   # use a real hook

With no --hook, a scratch state.sh is generated so the block is visible.
Everything is built in a temp tree; nothing under ~/.ccloop is touched.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ccloop import runner  # noqa: E402
from ccloop import transcript as tx  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--criteria", default="")
    ap.add_argument("--hook", default="")
    ap.add_argument("--no-hook", action="store_true")
    ap.add_argument("--session", type=int, default=3)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="ccloop-preview-"))
    try:
        run_dir = tmp / "proj" / ".ccloop" / "runs" / "preview-run"
        run_dir.mkdir(parents=True)
        (run_dir / "resume.md").write_text(
            "# Resume — run preview-run, after session 2\n\n"
            "## Original task\n\nread state.md\n\n"
            "## Continue\n\nContinue the original task from where the previous "
            "session stopped.\n",
            encoding="utf-8",
        )
        (run_dir / "criteria.md").write_text(args.criteria + "\n", encoding="utf-8")

        # Stage a prior session so the transcript-pointer block renders. HOME
        # is redirected into the temp tree first, so this reads a synthetic
        # transcript instead of whatever real sessions exist under ~/.claude.
        os.environ["HOME"] = str(tmp / "home")
        prior = tx.transcript_path("preview-previous-session")
        prior.parent.mkdir(parents=True, exist_ok=True)
        prior.write_text(
            "".join(
                json.dumps({"type": "assistant", "message": {"content": []}}) + "\n"
                for _ in range(1400)
            ),
            encoding="utf-8",
        )
        (run_dir / "sessions.log").write_text(
            "preview-previous-session\n", encoding="utf-8")

        if not args.no_hook:
            hook = run_dir.parent.parent / "state.sh"
            if args.hook:
                shutil.copyfile(args.hook, hook)
            else:
                hook.write_text(
                    "#!/bin/sh\n"
                    "echo 'OPEN DEFECTS: 4 — work in this order unless you state why not'\n"
                    "echo\n"
                    "echo '1. [critical] D-0001'\n"
                    "echo '   next: reproduce under the 4k-block mount'\n",
                    encoding="utf-8",
                )
            hook.chmod(0o755)

        sys.stdout.write(
            runner._build_prompt(run_dir / "resume.md", args.session, "preview-run")
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
