---
name: shipped-agents-embed-no-rules
description: RULE FOUR now requires rules embedded verbatim in agent definitions, but all four shipped agents (grind/scout/miner/memory-compactor) embed none.
metadata:
  type: project
tags: [claude-md, agents, rules]
---

## The gap

ccenv v0.31.0 added a bullet to RULE FOUR: subagents inherit CLAUDE.md as of
SESSION START, and `.claude/agents/*.md` is registered then too, so any rule a
delegated task could violate must be embedded verbatim in the agent definition.

None of the four shipped definitions in `/src/ccenv/agents/` does this today:

- `grind.md` — Bash/Read/Grep/Glob, model sonnet. Its own rules are about
  reporting discipline (raw evidence, never silently truncate, batch
  aggressively). Nothing about the git ban, the `find /` ban, the `rm` shape,
  or `python3`. It is the highest-risk one: a pure shell runner.
- `scout.md`, `miner.md`, `memory-compactor.md` — same situation, narrower
  blast radius.

Inheritance covers rules that existed before the session started, which is the
common case, so this is not currently broken in practice. It breaks for a rule
authored mid-session, and it breaks quietly.

## Which rules actually matter per agent

A delegated task can plausibly violate: the git ban (RULE TWO), the `find /`
ban (box overlay — already restates itself for subagents in its own last
bullet), the permission-prompt/`rm` shape (RULE SEVENTEEN), `python3` not
`python` (RULE SIXTEEN), and scripts-never-in-`/tmp` (RULE FIVE) for anything
that writes a helper.

The overlay's `find /` bullet is the existing precedent for how to say it:
"This applies to subagents too — do not delegate a `find /` you are not
allowed to run yourself."

## Not done

The user's instruction for the v0.31.0 change was explicitly to add the two
audited items and nothing adjacent, so the agent definitions were left alone
and the gap was reported instead.
