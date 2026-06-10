# awareness_hooks.py — automated awareness-doc maintenance

## Purpose

The three-layer awareness system (Constitution / Subsystem docs / Structural
map) is only valuable while the docs are current. The original design left
freshness entirely to the model voluntarily following the "Update Protocol" in
`SKILL.md` — which fails exactly when sessions get busy. `awareness_hooks.py`
makes maintenance **enforced** via three Claude Code hooks.

## The automatable / non-automatable split

This is the central design decision:

- **Layer 3 (structural map)** is pure AST extraction produced by
  `generate_structural_map.py`. It needs no judgment, so it is **regenerated
  outright** by the Stop hook whenever source changed.
- **Layers 1–2 (invariants, pitfalls, API intent, cross-subsystem
  assumptions)** are prose judgment no script can author. The hook therefore
  cannot write them — the most it can do is **refuse to let the model end a
  session** where it changed a subsystem's code without updating that
  subsystem's doc.

This mirrors the rest of the repo: ccloop's `keepgoing` Stop hook and
ccmemory's edit `guard` use the same "block, don't fabricate" philosophy.

## The three hooks

All are registered **globally** in `~/.claude/settings.json` and **self-gate**
on whether the current project (from the hook payload's `cwd`) has a
`.claude/awareness/` directory. In every other project they exit 0 immediately.

| Subcommand | Hook event | Responsibility |
|------------|-----------|----------------|
| `track` | PostToolUse (`Edit\|Write\|MultiEdit`) | Append touched source files and touched awareness docs to a per-session ledger under `.claude/awareness/.state/touched-<sid>.json` |
| `sync` | Stop | (1) regen Layer 3 once/session if source changed; (2) compute drift; (3) block with `{"decision":"block"}` if drifted docs remain under the nudge cap; (4) else stamp `[AWARENESS]` last-updated and allow the stop |
| `status` | SessionStart | Inject `additionalContext` listing subsystems whose source mtime is newer than their doc |

## file → subsystem mapping

Drift detection needs to know which subsystem a changed file belongs to. The
source of truth is the **`## Subsystems` markdown table in `CLAUDE.md`**
(`| Subsystem | Directory | Purpose |`) — a real structured table, far more
robust than parsing the prose file-lists inside each subsystem doc. A changed
file maps to the subsystem with the **longest matching directory prefix**. The
subsystem doc is `.claude/awareness/subsystems/<slug>.md` where `slug` is the
table name lowercased with non-alphanumerics collapsed to `-`. If the doc
doesn't exist, that subsystem is skipped (no point nudging toward a missing
file).

## Per-session ledger

`.claude/awareness/.state/touched-<session_id>.json`:

```json
{ "source": ["src/cache/cache.c"], "docs": [".claude/awareness/subsystems/cache.md"],
  "regen_done": true, "nudges": 1 }
```

- `source` / `docs` — accumulated by `track`.
- `regen_done` — ensures the structural map is regenerated at most once per
  session even across multiple Stop cycles.
- `nudges` — how many times `sync` has blocked this session; compared against
  the cap.

A subsystem is "drifted" when it has ≥1 file in `source` but its slug is not
represented in `docs`.

## Config / escape hatches (env)

| Var | Default | Effect |
|-----|---------|--------|
| `CCPROJECT_MAX_NUDGES` | 3 | Block re-feeds per session before `sync` gives up and allows the stop. 0 = unlimited. |
| `CCPROJECT_NO_ENFORCE` | unset | `=1` → never block (still regenerates the map). |
| `CCPROJECT_NO_AUTOREGEN` | unset | `=1` → skip structural-map regeneration. |

## Fail-open guarantee

`main()` wraps every subcommand in a bare `except` that returns 0. A bug in the
hook can never wedge a session — worst case the docs simply aren't enforced
that turn. All file I/O is individually guarded too.

## Installation

`install.sh` copies the script to
`~/.claude/skills/project-awareness/scripts/awareness_hooks.py` (alongside the
other analysis scripts — ccproject is skill-based, not a pip package) and
registers the three hooks by **absolute path** (`python3 <abs> <sub>`) so they
resolve regardless of Claude Code's `PATH`. Registration is idempotent and
self-healing: a moved script path replaces the stale entry on re-run, and
foreign hooks in the same event slot are preserved.

## History

- **1.0.0** — initial implementation. PostToolUse/Stop/SessionStart hooks;
  Layer-3 auto-regeneration; capped blocking enforcement for Layers 1–2;
  CLAUDE.md subsystem-table-driven file→subsystem mapping; metadata stamping.
