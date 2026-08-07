"""Session-maintained handoff document — the outgoing session's own words.

``summarize.py`` scrapes the transcript. That is derived, so it can never be
stale, but it also can never be insightful: the best it can do is replay the
last 4,000 chars of assistant text, which in practice is tool-result chatter
rather than intent. A session that writes its own handoff can state what it was
doing and why — and the file is on disk *before* the session dies, so it
survives the crash case where the scraper returns nothing at all.

Why this is a TIER and not a replacement for the scraped content:

``state.py``'s docstring already records the finding — "a document the outgoing
model has to remember to update eventually stops being updated, and a stale one
is worse than none" — which is why the forward-looking half became a computed
hook. The evidence held up when this module was written: mxfs's hand-maintained
``state.md`` had gone 7 days without a write across 36 ccloop runs, while the
``state.sh`` beside it ran fresh every session.

A stale handoff is byte-identical to a fresh one. The reader cannot tell, and
will act on six-sessions-ago intent as though it were current. So freshness is
established against the session that just ended, and a stale file is rendered
under an explicit marker rather than passed off as the session's parting words.
Same discipline as ``state.py``: render the problem INTO the block, never
swallow it.
"""

import os
from pathlib import Path

#: Filename the session writes, alongside the project's state.sh.
HOOK_NAME = "handoff.md"

#: Cap on what gets embedded. A session that writes a 200KB handoff has
#: defeated the purpose; truncation is visible, per _truncate.
DEFAULT_MAX_BYTES = 6000

SECTION = "## Handoff from the previous session"

#: Slack between the session's start time and the file's mtime, in seconds.
#: A handoff written moments before launch (by the runner's own bookkeeping,
#: or by a user staging one by hand) still counts as belonging to this session.
FRESHNESS_SLACK = 30


def handoff_path(run_dir):
    """Path to the handoff file for this run, honoring ``CCLOOP_HANDOFF_FILE``.

    Lives beside ``state.sh`` in ``<project>/.ccloop/`` — the session is told
    the absolute path in the prompt, so discovery is never its problem.
    """
    override = os.environ.get("CCLOOP_HANDOFF_FILE", "").strip()
    if override:
        return Path(override)
    return Path(run_dir).parent.parent / HOOK_NAME


def _max_bytes():
    try:
        return int(os.environ.get("CCLOOP_HANDOFF_MAX_BYTES", "").strip()
                   or DEFAULT_MAX_BYTES)
    except ValueError:
        return DEFAULT_MAX_BYTES


def _truncate(text, max_bytes):
    if max_bytes <= 0 or len(text) <= max_bytes:
        return text
    return (
        text[:max_bytes].rstrip()
        + f"\n\n_(truncated at {max_bytes} bytes — raise "
        "CCLOOP_HANDOFF_MAX_BYTES to see the rest)_"
    )


def read_handoff(run_dir, session_started=None):
    """Return ``(status, text, age_seconds)`` for the run's handoff file.

    ``status`` is one of ``missing``, ``fresh``, ``stale``, ``empty``,
    ``unreadable``. ``age_seconds`` is how long before the session STARTED the
    file was last written (0 when written during the session), or ``None`` when
    there is no file or no start time to compare against.

    With no ``session_started``, freshness cannot be established, so the file is
    reported ``stale``. Defaulting the other way would make every caller that
    forgets to thread the timestamp silently claim currency it never checked.
    """
    path = handoff_path(run_dir)
    if not path.is_file():
        return "missing", "", None

    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        mtime = path.stat().st_mtime
    except OSError as exc:
        return "unreadable", f"_(handoff file `{path}` could not be read: {exc})_", None

    if not text:
        return "empty", "", None

    if session_started is None:
        return "stale", text, None

    age = session_started - mtime
    if age <= FRESHNESS_SLACK:
        return "fresh", text, 0.0
    return "stale", text, age


def _fmt_age(age_seconds):
    if age_seconds is None:
        return "unknown age"
    hours = age_seconds / 3600.0
    if hours < 1:
        return f"{int(age_seconds // 60)} minutes before this session started"
    if hours < 48:
        return f"{hours:.1f} hours before this session started"
    return f"{hours / 24:.1f} days before this session started"


def handoff_block(run_dir, session_started=None):
    """Return ``(markdown, is_fresh)`` for the handoff section.

    ``is_fresh`` is what tells ``summarize`` whether it can drop the scraped
    ``last_text`` — a fresh handoff supersedes it, a stale one does not
    substitute for it.
    """
    status, text, age = read_handoff(run_dir, session_started)
    path = handoff_path(run_dir)

    if status == "missing":
        return "", False

    if status == "empty":
        return (
            f"\n{SECTION}\n\n_(handoff file `{path}` is empty — the previous "
            "session wrote nothing to it)_\n"
        ), False

    if status == "unreadable":
        return f"\n{SECTION}\n\n{text}\n", False

    if status == "fresh":
        return (
            f"\n{SECTION}\n\nWritten by the previous session itself, during that "
            f"session, at `{path}`. This is that session's own account of where it "
            "got to — prefer it over the scraped sections above where they "
            f"conflict.\n\n{_truncate(text, _max_bytes())}\n"
        ), True

    return (
        f"\n{SECTION}\n\n**STALE — do not read this as the previous session's "
        f"parting words.** `{path}` was last written {_fmt_age(age)}, so the "
        "session that just ended did NOT update it. Treat it as background that "
        "may have been overtaken; the scraped sections above describe what "
        f"actually just happened.\n\n{_truncate(text, _max_bytes())}\n"
    ), False
