---
name: compiled-claude-md-governance
description: How ~/.claude/CLAUDE.md is assembled/owned by install.sh, why repo CLAUDE.md is its verbatim source, and CLAUDE.md's content boundary (no changelog).
metadata:
  type: project
tags: [compiled, claude-md, install.sh, ccenv, documentation-hygiene]
---

Topic: how `~/.claude/CLAUDE.md` is assembled, owned, and content-bounded across the ccenv bundle.

## Assembly and ownership model (set 2026-06-13)

`install.sh` builds `~/.claude/CLAUDE.md` in two separate, non-overlapping steps, and the split is deliberate — a prior design that centralized both steps was rejected by the user:

- Top-level `install.sh` runs `assemble_ccenv_base_claude_md()` FIRST, before any component installer. It writes only the base: this repo's bundled `CLAUDE.md` plus user/system overlay blocks, inside a delimited `# [CCENV MANAGED]` … `# [/CCENV MANAGED]` region. On re-run it strips and regenerates only that region (via `awk`), preserving everything outside it (e.g. `[AWARENESS PROTOCOL]`) — this idempotency is what avoids backup churn.
- Each component installer owns and appends its OWN marker-delimited section after the managed region (e.g. `ccproject/install.sh` manages its own `# [AWARENESS PROTOCOL]` marker from its own snippet file). The top-level installer never reaches into a component's CLAUDE.md content, and a component never touches the top-level's managed region.
- The `CCPROJECT_SKIP_GLOBAL_CLAUDE_MD` gate variable was removed entirely for this reason and must not be reintroduced, nor any equivalent skip-coupling.

Why: the earlier design pulled `ccproject/global-claude-md-snippet.md` into the top-level, ran ccproject early behind a SKIP gate, and assembled the global file dead-last (line ~472) while a consumer (ccproject) ran earlier (line ~293). That ordering inversion produced a false "Global CLAUDE.md missing awareness protocol" verify failure. The user's direction: "submodules can install/update global CLAUDE.md as they wish ... installer should not install submodule CLAUDE.md changes." Rule going forward: a new component that needs global CLAUDE.md content gets its own marker-delimited, idempotent self-install in its OWN installer — never added to the top-level assembly, never re-centralized, never given a skip-gate.
[[install-claude-md-component-owned]]

## Repo CLAUDE.md is the managed block's verbatim source

Within the top-level's own managed region, `/src/ccenv/CLAUDE.md` is the byte-identical source: the assembly is marker header → verbatim `cat "$SCRIPT_DIR/CLAUDE.md"` → overlay blocks → closing marker. `install.sh` `cmp`s the assembled result against the installed file and no-ops when equal.

Consequence: an edit made directly inside the managed region of the INSTALLED `~/.claude/CLAUDE.md` survives only until the next `install.sh` run — any standing-order/policy change must also land in `/src/ccenv/CLAUDE.md` itself (which doubles as this repo's own project instructions). To verify the two are in sync: extract the region between markers in the installed file, drop the 2 header-comment lines, diff against the repo file.

Precedent that motivated writing this down (v0.13.3): the temp-file rule ("test scripts → project `tests/`, `/tmp` only for true one-shots") was edited in place in the installed `~/.claude/CLAUDE.md` in one session; a later session had to notice the drift, back-port the edit to the repo `CLAUDE.md`, bump bundle VERSION 0.13.2→0.13.3, and add CHANGELOG + docs/install.md entries. Treat any managed-region edit as provisional until it's ported to the repo source.
[[repo-claude-md-is-managed-block-source]]

## Content boundary: no version history in any CLAUDE.md

Separately from assembly mechanics, CLAUDE.md content itself is scoped: purpose, architecture, current conventions — never changelog or version-history content. This surfaced when `ccmemory/CLAUDE.md` had grown a 90-line "Architecture history" section listing every release v0.1.0–v0.9.0 with paragraphs of context per version. The user's objection: "why is history being kept in CLAUDE.md in _any_ project versus CHANGELOG.md???" — followed by a direction to clear all module-level CLAUDE.md files of history and remove them entirely, since their non-history content already duplicated into each module's README.md.

Why it matters beyond tidiness: CLAUDE.md loads into every session's context at start. Version-history paragraphs are overhead paid by every session for the benefit of the rare session doing release-management work — and that session can `Read` `CHANGELOG.md` on demand instead.

Rules that follow:
- Never add a "## Architecture history", "## Version history", or "## Changelog" section to any CLAUDE.md, top-level or per-module.
- A new release's notes go in that component's `CHANGELOG.md` (create one if absent; `## vX.Y.Z` headers, newest first).
- Per-module CLAUDE.md files in this repo are deprecated entirely. The top-level `/src/ccenv/CLAUDE.md` is the only global-rules file (the one installed to `~/.claude/CLAUDE.md` per the assembly model above); subdirectory architecture/install/test info belongs in that subdirectory's README.md, not a nested CLAUDE.md.
- Catch clause: if about to write a "this version did X, the next added Y" paragraph anywhere other than CHANGELOG.md or a commit message, stop.
[[no-version-history-in-claude-md]]
