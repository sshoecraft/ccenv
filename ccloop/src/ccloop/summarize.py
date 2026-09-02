"""Transcript JSONL -> resume.md transform.

Pure data transform, no LLM calls. ccloop runs this between sessions to
produce the next session's prompt from the prior session's transcript.

This document is an ORIENTATION LAYER, not the handoff. The handoff is the
transcript itself: the prompt preamble points the next session straight at
``<session-id>.jsonl`` (see ``runner.prior_session_block``), which holds
every prompt, tool call and result in full. Everything here is a cheap index
into that file — enough to know where the work was without paying to re-read
it, and enough to survive a session that never gets read.

What this file does NOT include, and why:

``Last 20 bash commands`` was removed in 0.20.0. Measured across mxfs's run
history it cost 465-803 tokens — roughly a third of the whole resume — to
deliver 20 commands clipped to 160 chars each. Nothing downstream ever needed
them; the transcript path is in the document for anyone who does.

A session-maintained handoff document was added in 0.20.0 and removed in
0.21.0. It made every session pay a continuous write tax — rewrite the whole
file whenever your understanding changes — to reproduce, badly and from
memory, what Claude Code was already writing to disk for free. Its path is
deliberately not named anywhere in this tree: a session that reads the name
goes looking for the file. See ``0.21.0`` in the bundle CHANGELOG.

``Files written or edited`` stays. It costs ~40 tokens (2% of the document),
it is derived so it cannot go stale, and it is the only durable answer to
"where was I".
"""

import os

from . import transcript as tx


def summarize(transcript_file, task, run_id="unknown", session_num="?", wedged=False):
    """Return a markdown resume document built from a session transcript."""
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

    if wedged:
        # The previous session was killed by a server-side safeguard flag. That
        # classifier evaluates the WHOLE assembled request, so the cheapest way
        # to trip it again is to carry the previous request's material forward.
        # Its last text turn is exactly that material, so it is withheld.
        text_block = (
            "_(withheld — the previous session was terminated by a server-side "
            "safeguard flag. Its final text is deliberately not carried forward: "
            "the classifier scores the whole assembled request, so replaying that "
            "content is what turns one flag into a chain of dead sessions.)_"
        )
    else:
        text = tx.last_text(transcript_file)
        if text.strip():
            text_block = text
        else:
            text_block = "_(no text turn — session may have crashed mid-tool)_"

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

## Last text from previous session

{text_block}

## Continue

Continue the original task from where the previous session stopped. This
summary is only an index — the full record is the transcript at the path
above, and the preamble ahead of this document tells you how to read it.
(Loop mechanics and how to signal DONE are in that preamble too.)
"""
