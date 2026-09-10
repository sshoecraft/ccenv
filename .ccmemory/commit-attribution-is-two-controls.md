---
name: commit-attribution-is-two-controls
description: Killing the Co-Authored-By trailer does NOT kill the Claude-Session URL — separate keys. attribution.sessionUrl defaults true; coAuthoredBy is reject…
metadata:
  type: project
tags: [settings, git, attribution, install]
---

## Two controls, not one

Claude Code appends two separate things to commit messages it writes:

- `Co-Authored-By: Claude … <noreply@anthropic.com>` — governed by
  `includeCoAuthoredBy`.
- `Claude-Session: https://claude.ai/code/session_…` — governed by
  `attribution.sessionUrl`, which **defaults to true**.

Setting `includeCoAuthoredBy: false` alone removes only the first. Verified
live on 2026-09-10: after that single edit, the harness's injected attribution
reminder dropped the co-author line and kept the `Claude-Session:` line. Both
disappeared only after `attribution.sessionUrl: false` was added.

The session URL is the one that matters — it writes a session identifier into
git history permanently and pushes it to whatever remote the repo has.

## The key name trap

`coAuthoredBy` (no `include` prefix) is what several blog posts and even a
web search summary recommend as "the modern key". **CLI 2.1.267 rejects it**:

```
Settings validation failed:
- : Unrecognized field: coAuthoredBy
```

The Edit tool validates settings.json against the CLI's schema and refuses the
write, so this fails loudly rather than silently — but do not trust the web on
this. The schema is embedded in the binary at
`~/.local/share/claude/versions/<ver>` and can be read with
`grep -ao "Claude-Session.\{0,60\}"` and friends. `includeCoAuthoredBy` is
marked deprecated in that schema and is still the only key that works.

## Where this is configured

Both live in `~/.claude/settings.json`. ccenv v0.32.0's `settings` step seeds
four keys: `includeCoAuthoredBy: false`, `attribution.commit: ""`,
`attribution.pr: ""`, `attribution.sessionUrl: false`. Seeded per sub-key, so
a deliberately-set commit trailer survives.

## Search gotcha

Do not grep the CLI binary with a greedy fixed-width lookbehind like
`grep -ao ".\{700\}sessionUrl"` — it backtracks catastrophically on a ~100MB
ELF and never returns. Anchor on the literal and take trailing context
instead.
