---
name: never-make-sessions-maintain-handoff-docs
description: NEVER instruct a session to write/update a handoff doc. The transcript is the handoff — point at it. ccenv v0.21.0 ripped this out of ccloop.
metadata:
  type: feedback
tags: [ccloop, handoff, tokens, prompt-design]
---

# Never make a session maintain a handoff document

User, blunt and repeated (2026-08-14): the handoff-document instruction "has
cost me millions in tokens". It is banned. This supersedes the 0.20.0 decision
recorded in `handoff-docs-must-be-freshness-stamped`.

## The rule

Do NOT put "keep a handoff/state/summary file current as you work" into any
prompt, preamble, skill, or hook. Not as a tier, not with a freshness check,
not "just a short one". Every such instruction charges output tokens on every
turn of every session to reproduce, from memory and worse, a document the
harness already writes for free.

## What to do instead

Claude Code writes a complete per-session transcript to
`~/.claude/projects/<cwd-slug>/<session-id>.jsonl` — every prompt, tool call,
tool result and reply. Locate the previous session deterministically and hand
the next session the path.

The user's own long-standing handoff has been exactly one line: "read previous
claude code session for context". It works. Encode that, don't reinvent it.

Deterministic location, in two tiers (ccloop `runner.prior_session_transcript`):

1. Within a run: `sessions.log` holds session-ids in order; the last one whose
   transcript still exists IS the predecessor. Walk backwards so a deleted
   transcript falls through instead of blanking the pointer.
2. First session of a run: newest non-empty `.jsonl` in
   `~/.claude/projects/<cwd-slug>/`, excluding this run's own ids.

Nothing to point at -> emit nothing. Never name a path that doesn't exist.

## Practical detail that makes it work

A transcript is multi-megabyte JSONL. "Read that file" alone will blow the
context it was meant to save, so the pointer must ship with size, line count,
a suggested `Read` offset near the tail (`lines - 300`), and a `grep` example.

## Why the transcript wins on every axis

- Free: the harness writes it, the model spends nothing.
- Cannot go stale: it *is* the session. No mtime checks, no STALE markers, no
  scraped fallback for when the model stopped maintaining the file — all of
  that 0.20.0 machinery was overhead for a document that never had to exist.
- Complete: tool results and reasoning, not a from-memory summary.

## Caveat that motivated the deterministic finder

Non-Anthropic API backends driving `claude` can't be relied on to know their
own transcript path or the convention. The wrapper computes it and states it;
the session never has to discover anything.
