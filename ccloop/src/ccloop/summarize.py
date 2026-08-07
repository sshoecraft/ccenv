"""Transcript JSONL -> resume.md transform.

Pure data transform, no LLM calls. ccloop runs this between sessions to
produce the next session's prompt from the prior session's transcript.

What this file does NOT include, and why:

``Last 20 bash commands`` was removed in 0.20.0. Measured across mxfs's run
history it cost 465-803 tokens — roughly a third of the whole resume — to
deliver 20 commands clipped to 160 chars each. Nothing downstream ever needed
them; the transcript path is in the document for anyone who does.

``Last text from previous session`` is now conditional. It hit its 4,000-char
cap on essentially every session, meaning it was never a summary — it was
whatever happened to fall in the last 4k chars of assistant output, usually
tool-result commentary. It stays as the FALLBACK when the session left no fresh
handoff of its own, because something is better than nothing, and it is the
only thing that works when a session crashes without writing one.

``Files written or edited`` stays. It costs ~40 tokens (2% of the document),
it is derived so it cannot go stale, and it is the only durable answer to
"where was I".
"""

import os

from . import handoff as ho
from . import transcript as tx


def summarize(transcript_file, task, run_id="unknown", session_num="?",
              run_dir=None, session_started=None):
    """Return a markdown resume document built from a session transcript.

    ``task`` is the original task text. ``run_dir`` and ``session_started``
    enable the handoff tier: without them the scraped fallback is always used,
    which is the safe direction — see ``handoff.read_handoff``.
    """
    session_id = os.path.basename(str(transcript_file))
    if session_id.endswith(".jsonl"):
        session_id = session_id[: -len(".jsonl")]

    ctx = tx.context_tokens(transcript_file)
    ctx_str = str(ctx) if ctx is not None else "unknown"

    counts = tx.tool_counts(transcript_file)
    if counts:
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        tools_str = " ".join(f"{name}×{n}" for name, n in ordered)
    else:
        tools_str = "none"

    files = tx.files_edited(transcript_file)
    if files:
        files_block = "\n".join(f"- {f}" for f in files)
    else:
        files_block = "_(none)_"

    handoff_block, handoff_is_fresh = ("", False)
    if run_dir is not None:
        handoff_block, handoff_is_fresh = ho.handoff_block(run_dir, session_started)

    # A fresh handoff is the session's own account and supersedes the scrape.
    # A stale or missing one does not, so the fallback stays.
    if handoff_is_fresh:
        text_section = ""
    else:
        text = tx.last_text(transcript_file)
        if text.strip():
            text_block = text
        else:
            text_block = "_(no text turn — session may have crashed mid-tool)_"
        text_section = f"""
## Last text from previous session

{text_block}
"""

    return f"""# Resume — run {run_id}, after session {session_num}

## Original task

{task}

## Previous session

- session-id: `{session_id}`
- transcript: `{transcript_file}`
- approx context at last assistant turn: {ctx_str} tokens
- tools used: {tools_str}

## Files written or edited in the previous session

{files_block}
{handoff_block}{text_section}
## Continue

Continue the original task from where the previous session stopped. The
previous session's transcript is at the path noted above — you may Read
it if you need full detail on what was done. (Loop mechanics and how to
signal DONE are in the wrapper preamble above this summary.)
"""
