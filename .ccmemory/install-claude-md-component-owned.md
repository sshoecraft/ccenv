---
name: install-claude-md-component-owned
description: Top-level install.sh owns only the BASE ~/.claude/CLAUDE.md (in a [CCENV MANAGED] marker region); each component owns/appends its own section.
metadata:
  type: feedback
---

Ownership model for `~/.claude/CLAUDE.md` assembly (set 2026-06-13, at the user's explicit direction):

- Top-level `install.sh` assembles ONLY the base — this repo's bundled `CLAUDE.md` plus user/system overlay blocks — and does it FIRST, **before** any component installer runs (function `assemble_ccenv_base_claude_md`).
- The base is written inside a delimited `# [CCENV MANAGED]` … `# [/CCENV MANAGED]` region. On re-run the top-level refreshes ONLY that region (awk strips the old managed block, regenerates it, preserves everything outside). This is what makes it idempotent — no backup churn.
- Each component installer owns and appends its OWN section after the managed region (e.g. `ccproject/install.sh` step 4 manages its `# [AWARENESS PROTOCOL]` marker idempotently from its own `global-claude-md-snippet.md`). The top-level NEVER reaches into a component's CLAUDE.md content.
- The `CCPROJECT_SKIP_GLOBAL_CLAUDE_MD` gate variable was REMOVED entirely — do not reintroduce it or any equivalent "skip" coupling.

**Why:** the previous design had the top-level pull `ccproject/global-claude-md-snippet.md` and run ccproject early with a SKIP gate, then assemble the global file dead-last (line ~472) while a consumer (ccproject) ran at line ~293. That inversion caused a false "Global CLAUDE.md missing awareness protocol" verify failure, and centralizing a component's content in the top-level violates separation of ownership. The user rejected it: "submodules can install/update global CLAUDE.md as they wish ... installer should not install submodule CLAUDE.md changes."

**How to apply:** when a new component needs global CLAUDE.md content, give it its own marker-delimited, idempotent self-install in its OWN installer; never add it to the top-level assembly, never re-centralize, never add a skip-gate. Same spirit as [[mcp-heal-stale-command-pattern]] — this structure is load-bearing, don't "simplify" it away. Related: [[no-per-component-venvs]].
