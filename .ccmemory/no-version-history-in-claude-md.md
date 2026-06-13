---
name: no-version-history-in-claude-md
description: Version history goes in CHANGELOG.md, not CLAUDE.md. CLAUDE.md is for purpose/architecture/conventions, never for changelog content.
metadata:
  type: feedback
tags: [docs, convention, claude.md, changelog]
---

CLAUDE.md is project documentation for Claude — purpose, architecture, current conventions. It is NOT a place for version history or changelog content. The standard place for changelogs is `CHANGELOG.md` at the same level as the package's `README.md`.

This came up when `ccmemory/CLAUDE.md` had a 90-line "Architecture history" section that listed every release from v0.1.0 to v0.9.0 with paragraphs of context for each one. The user called it out: "why is history being kept in CLAUDE.md in _any_ project versus CHANGELOG.md???" — and then directed that all module-level CLAUDE.md files be cleared of history and removed entirely (the project-level architecture content already duplicates into README.md anyway; the only unique content was the changelog).

**Why:** CLAUDE.md gets loaded into Claude's context window on session start. Version-history paragraphs are pure overhead — the model rarely needs to know what v0.4.0 did. The only sessions that need that information are ones doing release-management work, and those can Read `CHANGELOG.md` on demand. Putting changelogs in CLAUDE.md costs every session for the benefit of a small minority.

**How to apply:**

- Never add a "## Architecture history", "## Version history", "## Changelog" or similar section to any CLAUDE.md file (top-level OR per-module).
- When releasing a new version of a component, add the entry to that component's `CHANGELOG.md` (create one if it doesn't exist; use standard markdown with `## vX.Y.Z` headers, newest at top).
- Per-module CLAUDE.md files in this repo are deprecated entirely. The top-level `/src/ccenv/CLAUDE.md` is the global-rules file installed to `~/.claude/CLAUDE.md`; subdirectory architecture/install/test info belongs in that subdirectory's `README.md`.
- If you find yourself about to write a "this version did X, the next version added Y" paragraph anywhere other than CHANGELOG.md or a commit message, stop.

Related: [[ccenv-installed-vs-source-version]] — same family of "documentation hygiene" / "single source of truth per concern".
