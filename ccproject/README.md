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

```
/project:update-awareness    # Update docs from recent git changes
/project:refresh-map         # Regenerate the structural map
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
