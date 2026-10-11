---
name: compiled-verification-and-restraint-feedback
description: Assorted user corrections: verify our own prose, doc placement, hook gates, installer restraint, no handoff docs, scripts not in /tmp, fixture-only d…
metadata:
  type: feedback
tags: [compiled, feedback, verification, installer, hooks, docs]
---

Assorted group (clusterer tail): nine user corrections with no single topic. Common thread: do not assume, do not enforce, do not over-reach. Organised by theme.

## Verify before relaying
- A rationale in our own CHANGELOG, docs or docstrings is a claim, not evidence. Check external-policy claims against the vendor page (web search) and behavioral claims against a transcript or measurement. [[changelog-rationale-is-not-evidence]]
  - False claim 1: `claude -p` bills a metered API pool. The change was paused and never took effect; ccmemory's compile path and ccloop's `--headless` gate were wrongly built on it (fixed in bundle v0.38.0).
  - False claim 2: compactor agents block session start ~90s. No transcript was cited; they always ran in the background. Removal of automatic compaction (v0.20.0) let one backlog hit 165 notes and overflow even a feedback-only listing; restored v0.21.0. Agent-tool subagents never involve `claude -p`.
  - The user had to say "do a damn web search" twice. When the only source is our own prose, say so.
- ccloop `--cutoff`: 500 to 145 and back to 500 was tried and lost to restart churn (about 93% fixed-context overhead at 145k vs about 16% at 500k). Measured startup context S = 65k median, about 75% of it CLI floor that grows about 1.3k per release; ours is about 8k. Never propose lowering the cutoff; the only remaining lever is context growth per request (delegate grind output to subagents). Analysis: `docs/context-economics.md`. [[ccloop-cutoff-lowering-already-tried-and-lost]]

## Where things go
- No version history in any CLAUDE.md (top-level or per-module): it is loaded every session for the benefit of release work only. Releases go in `CHANGELOG.md`; module info goes in that directory's `README.md`. [[no-version-history-in-claude-md]]
- Never instruct a session to maintain a handoff/state doc; it costs output tokens every turn (user: "millions in tokens"). The transcript `~/.claude/projects/<cwd-slug>/<session-id>.jsonl` is the handoff. ccloop locates the predecessor deterministically (sessions.log walked backwards, else newest non-empty jsonl excluding own ids), emits nothing if none, and ships size, line count, a tail offset and a grep example with the pointer. Superseded the 0.20.0 freshness-stamp design (v0.21.0). [[never-make-sessions-maintain-handoff-docs]]
- Scripts are never temp files; the split is by kind, not predicted lifetime. Harnesses, probes and repros go in `tests/`; utilities in `tools/` or `scripts/`; `/tmp` and the harness scratchpad are for data only. A destructive-test fixture tree is data and may live in /tmp; the script operating on it may not. [[scripts-never-go-in-tmp]]

## Enforcement and installers
- A gate whose predicate is "the doc was touched" is satisfied by a no-op write. Measured over 810 sessions: 32 hit the 3-nudge cap and passed with no update. ccproject's awareness hooks were removed in v0.33.0. Also: per-session state files with no cleanup (810 files, 141 KB apparent vs 3.3 MB on disk; quote both `du` views) and ungitignored; mtime is not a drift signal (checkouts, NFS). Removing a hook from an installer requires converting its step to unregister (strip only hooks naming the script, drop emptied entries, delete the stale script), tested against a copy of a REAL settings.json. [[enforcement-hooks-that-check-file-written]]
- Installers seed, never overwrite: a key already present in `~/.claude/settings.json` (or any user config file) is the user's choice; print `already set, left alone: <key>=<value>` and do not rewrite the file if nothing was added. Exception: wiring ccenv fully owns (its own hooks, MCP registrations). Tested by `tests/test_settings_step.sh`. [[installer-seeds-settings-never-overwrites]]
- Copy-paste commands in install output must be one physical line, never `\` continuations. [[one-line-copy-paste-commands]]

## Destructive scripts
- Real runs only in a throwaway fixture; `--dry-run` on the live system. Never `-y`/`--force` for testing. `--only X` scopes component, not directory, and an incident with `uninstall.sh` deleted live hooks, MCP registrations and an unbacked-up `.ccprospect/` state dir. "Wanted gone eventually" is not authorization now. Uninstallers must archive state (tar, abort deletion if tar fails) since git is not a restore path. [[test-destructive-scripts-against-fixtures-only]]
