# Memory compaction (compile-memories)

## What it does

Raw per-session ccmemory notes (one fact per `.md` file) pile up over time.
Compaction folds a batch of related raw notes into ONE dense, deduplicated,
cross-referenced `compiled-<topic>` article so the index stays useful. The raw
notes stay as the source of truth — the compiled article is additive.

## Architecture

Three pieces, no LLM subprocess:

- **`ccmemory/compile.py`** — backlog detection + candidate selection + the
  shared `COMPILER_PROMPT`. No LLM call.
  - `count_backlog(memory_dir)` → `{backlog, total_raw, has_compiled, threshold}`.
    Backlog = raw memories newer than the most recent `compiled-*` article (or
    all raw memories when nothing is compiled yet).
  - `compile_status(memory_dir, topic, max_inputs)` → backlog + candidate input
    names + a `how` pointer. Read-only; this is what `ccmemory compile` prints.
  - `threshold()` reads `CCMEMORY_COMPILE_THRESHOLD` (default 20).
  - A `compiled-` **name prefix** marks an article as compiled. `memory_write`
    has no subdir support, so compiled articles live at the memory-dir root, not
    in a `compiled/` subdirectory.
- **`ccmemory/hooks.py` → `session_handler`** — appends a one-line nudge to the
  SessionStart `additionalContext` when `backlog >= threshold`. Fail-open
  (`_compaction_nudge` swallows errors → no nudge). Under threshold it injects
  nothing.
- **`skills/compile-memories/SKILL.md`** — the actual compaction procedure, run
  by the interactive session. Reads memories via `memory_list`/`search`/`get`,
  synthesizes per `COMPILER_PROMPT`, writes via `memory_write`
  (`name: compiled-<topic>`, `type: project`, `tags: [compiled, ...]`).
  Installed to `~/.claude/skills/compile-memories/` by the top-level
  `install.sh`.

## Two-layer "when to use"

A skill with no trigger never gets invoked, so compaction has two triggers that
both reference the same threshold:

1. **Active push** — the SessionStart hook nudge, fired off the live backlog count.
2. **Passive trigger** — the skill's own description lists trigger phrases so it
   auto-activates when the user asks or the nudge appears.

## Why no `claude -p`

The original `compile.py` shelled out to a headless `claude -p` subprocess.
Anthropic is moving the Agent SDK / `claude -p` / Claude Code GitHub Actions off
subscription usage onto a separate metered monthly credit pool (full API rates,
no rollover), so every compile run would burn that credit. Compaction now runs in
the live INTERACTIVE session (unaffected by the change) — zero `claude -p`, zero
metered credit, full LLM-quality synthesis.

## Why count the backlog, not the total

Compiled articles are additive — they never delete the raw notes. A naive
`total > N` check would fire the nudge forever once crossed, even right after
compacting. Counting raw memories newer than the most recent compiled article
makes the nudge self-resetting: compile, and the backlog drops to ~0 until new
notes accumulate.

## History

- v0.10.0 — removed the `claude -p` path; added `compile-memories` skill,
  backlog-threshold SessionStart nudge, and read-only `ccmemory compile` status.
  Previously (`v0.9.0` and earlier) `compile.py` ran `claude -p` and the
  `ccmemory compile` CLI produced the article directly.
