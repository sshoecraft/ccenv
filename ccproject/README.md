# Project Awareness System for Claude Code

A skill that teaches Claude Code to build and maintain a three-layer knowledge
architecture for any project. Gives Claude both the 30,000-foot architectural
view AND the ability to drill into specifics — without blowing the context window.

## The Problem

Codebases outgrow a single context window. Claude Code starts missing things —
fixes 5 call sites, misses the 6th, declares victory. No single agent can hold
an entire non-trivial system in working memory.

## The Solution

Three layers of persistent project knowledge, maintained by Claude Code itself:

1. **Constitution** (`CLAUDE.md`, ~200 lines, always loaded) — project overview,
   subsystem inventory, architectural invariants, routing table.

2. **Subsystem Docs** (`.claude/awareness/subsystems/*.md`, loaded on demand) —
   per-subsystem API, internals, pitfalls, cross-dependencies, historical bugs.

3. **Structural Map** (`.claude/awareness/structural-map.md`, loaded on demand) —
   compressed AST-level view of the entire codebase: function signatures, type
   definitions, call graph. Target <5% of raw source token count.

## Installation

```bash
git clone https://github.com/sshoecraft/ccproject.git
cd ccproject
./install.sh
```

This installs:
- The skill to `~/.claude/skills/project-awareness/`
- Analysis scripts to `~/.claude/skills/project-awareness/scripts/`
- `ccproject_defects.py` and `ccproject_criteria.py` to `~/.local/bin` (or `$PYTHONUSERBASE/bin`)
- The global awareness protocol to `~/.claude/CLAUDE.md`
- Templates for all generated documents

Requires Python 3.6+. Optionally install tree-sitter for more accurate parsing:
```bash
pip install tree-sitter tree-sitter-c tree-sitter-cpp
```

Works on any system with Claude Code (macOS, Linux, WSL).

## Usage

### Bootstrap a project

Open Claude Code in any project directory:

```
Bootstrap awareness for this project
```

Claude Code will scan the project, identify subsystems (asking you to confirm),
and generate all three layers. Review the invariants section — Claude catches
the obvious ones, but you know things the code doesn't say.

### Daily use

Just start Claude Code normally. It reads the CLAUDE.md, sees the `[AWARENESS]`
section, and follows the routing table to load relevant subsystem docs for your
task automatically.

### Maintenance

Maintenance is manual. ccproject registers no hooks and runs nothing in the
background — installing it copies a skill, some templates and three analysis
scripts, and that is all.

It used to enforce freshness with three global hooks, and they were removed:

- `track` (PostToolUse) wrote a per-session ledger to
  `.claude/awareness/.state/`, one file per session, and nothing ever deleted
  one. 810 accumulated in a single project. Nothing read them once the session
  ended.
- `sync` (Stop) blocked the session from ending until a subsystem doc was
  edited. It could check only whether the doc had been written, not whether the
  edit was true, so a no-op write satisfied it. Across those 810 sessions, 32
  burned the full three-block cap and still ended with no doc update.
- `status` (SessionStart) injected an mtime-based drift report into the model's
  context. mtime is not a drift signal — a checkout or a reformat bumps every
  source file at once, flagging everything.

Regenerate the structural map when you want it (pure AST extraction, no
judgment):

```
python3 ~/.claude/skills/project-awareness/scripts/generate_structural_map.py <project>
```

The prose layers — invariants, pitfalls, API intent — were never script-generable
and still aren't. Keeping them current is a judgment call, made deliberately.

Tuning (env vars):

| Var | Default | Effect |
|-----|---------|--------|
| `CCPROJECT_MAX_NUDGES` | 3 | Stop-hook re-feeds before it gives up and lets the session end (0 = unlimited) |
| `CCPROJECT_NO_ENFORCE` | unset | `=1` disables the blocking behavior (still regenerates the map) |
| `CCPROJECT_NO_AUTOREGEN` | unset | `=1` disables structural-map regeneration |

The slash commands still work for explicit, on-demand passes:

```
/project:update-awareness    # Review + update docs from recent changes
/project:refresh-map         # Regenerate the structural map by hand
```

### Agent Teams

For cross-cutting tasks, the CLAUDE.md includes a team configuration:
- **Team lead** loads Constitution + Structural Map
- **Workers** load Constitution + their Subsystem Doc
- Workers update their subsystem doc before reporting completion
- Only the team lead updates CLAUDE.md

## Analysis Scripts

The system ships with Python scripts that automate the bootstrap and maintenance process:

- **`analyze_project.py`** — Auto-detects languages, build systems, subsystem boundaries, and inter-subsystem dependencies. Outputs JSON.
- **`generate_structural_map.py`** — Extracts function signatures, type definitions, and call graph edges. Outputs compact notation and Mermaid diagrams. Uses tree-sitter when available, falls back to regex.
- **`generate_mermaid_callgraph.py`** — Generates visual call graph diagrams in Mermaid format. Supports filtering by subsystem and cross-subsystem-only views.

These scripts can also be used standalone:

```bash
# Analyze a project
python3 ~/.claude/skills/project-awareness/scripts/analyze_project.py ~/src/myproject

# Generate structural map with Mermaid
python3 ~/.claude/skills/project-awareness/scripts/generate_structural_map.py ~/src/myproject --mermaid --stdout

# Cross-subsystem call graph
python3 ~/.claude/skills/project-awareness/scripts/generate_mermaid_callgraph.py \
    .claude/awareness/structural-map.md --cross-subsystem-only

# Subsystem interaction summary
python3 ~/.claude/skills/project-awareness/scripts/generate_mermaid_callgraph.py \
    .claude/awareness/structural-map.md --summary
```

Supported languages: C, C++, Python, JavaScript/TypeScript, Go, Rust.
C/C++ is the primary focus — the scripts handle multi-line declarations,
function pointers, macro-defined functions, and header/source separation.

## Defect Queue and Criteria Board

Two commands, installed onto PATH (`~/.local/bin`) by the installer, that keep a
project's working state in the project itself:

- **`ccproject_defects.py`** — what is broken and still needs work, in
  `.ccproject/defects.json`. A defect is open until `remove` takes it out with
  the reason; there is no status field.
- **`ccproject_criteria.py`** — what the project must achieve and where each
  criterion stands, in `.ccproject/criteria.json`.

The project is the nearest directory at or above the cwd that already has a
`.ccproject/`, else the cwd (created on the first write), so commands work from
any subdirectory once the store exists. Every write is under a lock and
replaces the file atomically. Fields neither tool knows are kept, so an existing
`defects.json` / `criteria.json` from a project's own tools imports unchanged.

```bash
ccproject_defects.py                          # the queue, severity order
ccproject_defects.py -d                       # with summary and next step
ccproject_defects.py add -m "Mount hangs after failover" -s high -w "evidence" -n "next step"
ccproject_defects.py update ID -W "more evidence"    # -w replaces (needs --replace), -W appends
ccproject_defects.py remove ID --fixed --why "what proved it"   # prints the CHANGELOG line
ccproject_defects.py --gate                   # exit 1 while any defect blocks
ccproject_defects.py gate-exclude scope harness       # values of a field that do not block
ccproject_defects.py import path/to/defects.json

ccproject_criteria.py                         # the board and its VERDICT line
ccproject_criteria.py -v                      # requirement, why, detector, by phase
ccproject_criteria.py add -i boots -r "it boots" -d tests/boot.sh -p startup
ccproject_criteria.py update boots -s PASS -m "booted in 3s" -w "10 runs"
ccproject_criteria.py --gate                  # exit 1 unless every criterion is green
ccproject_criteria.py --gaps                  # no detector, or detector script missing
ccproject_criteria.py import path/to/criteria.json
```

**Matrix criteria.** A project may declare one axis — class, config, platform —
with `ccproject_criteria.py axis class warrior mage priest`. A row added with
`--per-cell` then keeps one result per value under `per_class`, written with
`update ID -s PASS --cell mage` (`--at mage` and `--class mage` — the axis
name — are the same option).
The row is PASS when every value is green, FAIL when any is FAIL, PARTIAL
otherwise; `update ID --values a b` narrows a row to some of the values. The
first per-value write to a plain row makes it a matrix row.

**Linking.** `ccproject_defects.py add/update --criterion ID` names a criterion
a defect breaks; the board shows it as `blocked by D-…`.

**Harness runs.** `pending ID… --run-id R`, `executing ID --run-id R`, then
`finalize --run-id R` turns anything left in flight into INCOMPLETE (started,
never reported) or NOT RUN (never started).

**Builds.** `update ID -s PASS --build B` records the build a result was
measured on. `ccproject_criteria.py --build B` (with or without `--gate`)
grades against build B: a green recorded on any other build, or on none, reads
STALE. The stored result is never rewritten.

**Gates.** `ccproject_defects.py --gate` fails while any defect blocks. By
default every defect blocks. Two filters narrow that:
- `gate-exclude FIELD VALUE…`: a record whose field has one of the values does
  not block. A record missing the field still blocks.
- `gate-require FIELD VALUE…`: a record blocks only if its field has one of the
  values. A record missing the field doesn't block, and the gate prints a NOTE
  counting those unrated records.

For example, `gate-require impact integrity stability` plus
`gate-exclude scope harness` blocks only integrity/stability defects outside
the test harness. `ccproject_criteria.py --gate` fails unless every criterion
is green; an empty board fails too.

**One release verdict.** "No open blocking defects" is a release criterion like
any other. When the project keeps a defect queue, the board prints it as its
own line, and the VERDICT counts it, so `ccproject_criteria.py --gate` is the
whole release check. Set `"defects_gate": false` in `criteria.json` for a
project whose queue is not a release bar.

**Project data.** The key `"project"` is reserved for the project at every level:
on the board, a criterion, a cell, the defect queue, or a defect. Neither tool
ever reads or rewrites it, and no generic field will ever be named that.

### Project drivers

A project whose harness needs more than the generic model adds
`.ccproject/driver.py`. Both tools load it. It declares `API = 1` and defines
any of these hooks. A hook that is missing, or that returns `None`, means
"do it the generic way".

| Hook | Called | Returns |
|---|---|---|
| `applies(data, row)` | for the axis values a matrix row must pass | list of values |
| `grade(data, row, value, record, build)` | for each row (`value` None) or cell (`record` None if never written) the board grades | status |
| `on_result(data, row, value, record, args)` | after a result is written, before the save, with the lock held | — |
| `blocks(data, entry, args)` | for each defect the gate considers, in both tools | bool |
| `options(parser)` | while building the top-level parser: add flags | — |
| `commands(subparsers)` | while building subcommands: add your own | — |

- **Driver subcommands:** one marks itself with `set_defaults(driver_command=fn)`. `fn(data, args)` runs with the lock held and returns the exit code.
- **Tool access:** the tool's module is available to the driver as `ccproject` (`ccproject.save(data)`, `ccproject.find(...)`, `ccproject.project_store()`).
- **Refusals:**
  - a driver that redefines a built-in subcommand or flag
  - a driver that declares a different `API`

The driver is project code, and it runs whenever either tool runs in that tree,
with the same trust as the project's own tests.

## File Layout After Bootstrap

```
your-project/
├── CLAUDE.md                              # Layer 1: Constitution
├── .claude/
│   ├── awareness/
│   │   ├── structural-map.md              # Layer 3: Compressed code map
│   │   ├── structural-map.mermaid         # Visual call graph (Mermaid)
│   │   ├── subsystems/
│   │   │   ├── cache.md                   # Layer 2: Subsystem doc
│   │   │   ├── dlm.md                     # Layer 2: Subsystem doc
│   │   │   └── ...
│   │   └── templates/                     # Local copy of templates
│   └── commands/
│       └── project/
│           ├── bootstrap-awareness.md
│           ├── refresh-map.md
│           └── update-awareness.md
└── src/
    └── ...
```

## Why Not Just Use a Bigger Context Window?

Even at 1M tokens, the "lost in the middle" problem means the model ignores
information in the center of long contexts. Agents make locally sensible but
globally inconsistent decisions. More tokens doesn't fix comprehension.

The point isn't to fit everything in — it's to load only what's relevant for
the current task, and ensure the most critical knowledge (invariants, design
tensions, pitfalls) is always at the top of context where attention is strongest.

## License

MIT
