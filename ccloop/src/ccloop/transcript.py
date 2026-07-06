"""Helpers for locating and reading Claude Code session transcripts.

Claude Code writes a per-session JSONL transcript to
``~/.claude/projects/<cwd-slug>/<session-id>.jsonl``. The slug is the
working directory with every character that is not alphanumeric or a dash
replaced by a dash. These helpers reproduce that path and extract the few
facts ccloop needs (token usage, tool calls, text turns).
"""

import json
import os
import re
from pathlib import Path


def cwd_slug(cwd=None):
    """Reproduce Claude Code's per-project directory name for ``cwd``."""
    real = os.path.realpath(cwd) if cwd else os.path.realpath(os.getcwd())
    return re.sub(r"[^A-Za-z0-9-]", "-", real)


def transcript_path(session_id, cwd=None):
    """Absolute path to the transcript JSONL for ``session_id`` under ``cwd``."""
    return Path.home() / ".claude" / "projects" / cwd_slug(cwd) / f"{session_id}.jsonl"


def iter_events(path):
    """Yield parsed JSON objects from a transcript, skipping bad lines."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _assistant_content(event):
    # Synthetic API-error turns (e.g. the "Prompt is too long" context-wall
    # marker Claude Code injects when the window fills) are not real assistant
    # output — skip them so summaries, edit lists and turn counts never treat
    # them as work.
    if event.get("type") != "assistant" or event.get("isApiErrorMessage"):
        return []
    return event.get("message", {}).get("content") or []


CONTEXT_WALL_TEXT = "Prompt is too long"


def hit_context_wall(path, tail_bytes=131072):
    """True if the session hit Claude Code's hard context wall.

    When the window fills with auto-compact disabled (ccloop always sets
    ``DISABLE_AUTO_COMPACT=1``), Claude Code injects a synthetic assistant
    turn flagged ``isApiErrorMessage`` whose only text is exactly
    ``Prompt is too long``, then idles on "Context limit reached · /compact
    or /clear". That event — not any token estimate — is the deterministic
    signal that the session can make no further progress and must be relayed.

    Only the tail is scanned (the marker is always among the last events) so
    the interactive watcher can poll this cheaply on a large transcript.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            chunk = fh.read()
    except OSError:
        return False
    text = chunk.decode("utf-8", "replace")
    if size > tail_bytes:
        # Drop the partial first line left by seeking into the middle.
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else ""
    for line in text.splitlines():
        line = line.strip()
        # Cheap pre-filter so we only JSON-parse candidate lines.
        if not line or '"isApiErrorMessage"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant" or not event.get("isApiErrorMessage"):
            continue
        for block in (event.get("message", {}) or {}).get("content") or []:
            if block.get("type") == "text" and CONTEXT_WALL_TEXT in (block.get("text") or ""):
                return True
    return False


def last_api_error(path, tail_bytes=131072):
    """The text of a terminal API-error turn IF it is the last real turn.

    When a turn aborts on a transport/API error (e.g. "API Error: The
    operation timed out.", an overload, or a 5xx), Claude Code commits a
    synthetic assistant turn flagged ``isApiErrorMessage`` and then idles at
    the prompt. Unlike the context wall it does NOT relay, and unlike a normal
    turn-end it does NOT emit a Stop event (the turn aborted, it did not end),
    so the ``keepgoing`` hook never fires either — the session simply wedges
    until a human nudges it.

    Returns the error text when that API-error turn is the *last* real turn
    (no newer assistant/user/tool event) and is NOT the context wall
    (``CONTEXT_WALL_TEXT``, owned by ``hit_context_wall``); otherwise ``None``.
    The "last real turn" guard means an error Claude Code already retried past
    (a newer turn exists) yields ``None`` — so this never flags a session that
    recovered on its own. Auxiliary records (``mode``/``permission-mode``/
    ``last-prompt``) are not real turns and are ignored.

    Only the tail is scanned so the interactive watcher can poll this cheaply.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            chunk = fh.read()
    except OSError:
        return None
    text = chunk.decode("utf-8", "replace")
    if size > tail_bytes:
        # Drop the partial first line left by seeking into the middle.
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else ""
    last = None
    for line in text.splitlines():
        line = line.strip()
        if not line or '"type"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Real turns only. Tool results arrive as user-role messages, so a tool
        # call after the error correctly counts as a newer turn.
        if event.get("type") in ("assistant", "user"):
            last = event
    if (last is None or last.get("type") != "assistant"
            or not last.get("isApiErrorMessage")):
        return None
    for block in (last.get("message", {}) or {}).get("content") or []:
        if block.get("type") == "text":
            txt = block.get("text") or ""
            return None if CONTEXT_WALL_TEXT in txt else txt
    return None


def context_tokens(path):
    """Total context tokens at the last assistant turn that reported usage.

    Sum of input + cache-creation + cache-read tokens, which is how Claude
    Code accounts for the live context window. Returns ``None`` if no usage
    data is present. Synthetic API-error turns (the context-wall marker
    carries an all-zero usage block) are skipped so they can't zero out the
    real figure.
    """
    total = None
    for event in iter_events(path):
        if event.get("type") != "assistant" or event.get("isApiErrorMessage"):
            continue
        usage = event.get("message", {}).get("usage")
        if not usage:
            continue
        total = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
        )
    return total


def last_text(path, limit=4000):
    """Concatenated assistant text turns, trimmed to the last ``limit`` chars."""
    chunks = []
    for event in iter_events(path):
        for block in _assistant_content(event):
            if block.get("type") == "text" and block.get("text"):
                chunks.append(block["text"])
    text = "\n".join(chunks)
    return text[-limit:] if len(text) > limit else text


def files_edited(path):
    """Distinct file paths touched by Write/Edit/MultiEdit/NotebookEdit, in order."""
    seen = []
    edit_tools = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
    for event in iter_events(path):
        for block in _assistant_content(event):
            if block.get("type") != "tool_use" or block.get("name") not in edit_tools:
                continue
            inp = block.get("input") or {}
            fp = inp.get("file_path") or inp.get("notebook_path")
            if fp and fp not in seen:
                seen.append(fp)
    return seen


def bash_commands(path, last=20, width=160):
    """Last ``last`` Bash commands, newlines flattened, each clipped to ``width``."""
    cmds = []
    for event in iter_events(path):
        for block in _assistant_content(event):
            if block.get("type") != "tool_use" or block.get("name") != "Bash":
                continue
            cmd = (block.get("input") or {}).get("command")
            if cmd:
                cmds.append(" ".join(cmd.split("\n"))[:width])
    return cmds[-last:]


def tool_counts(path):
    """Mapping of tool name -> call count across all assistant turns."""
    counts = {}
    for event in iter_events(path):
        for block in _assistant_content(event):
            if block.get("type") == "tool_use":
                name = block.get("name", "?")
                counts[name] = counts.get(name, 0) + 1
    return counts


def assistant_turns(path):
    """Number of real assistant turns (a rough progress signal).

    Synthetic API-error turns (the context-wall marker) are excluded so a
    session that produced nothing but a "Prompt is too long" error does not
    read as having done work.
    """
    return sum(
        1 for e in iter_events(path)
        if e.get("type") == "assistant" and not e.get("isApiErrorMessage")
    )
