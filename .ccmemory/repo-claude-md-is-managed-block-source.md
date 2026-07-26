---
name: repo-claude-md-is-managed-block-source
description: /src/ccenv/CLAUDE.md is the verbatim source of the [CCENV MANAGED] block in ~/.claude/CLAUDE.md — policy edits must land there or reinstall wipes the…
metadata:
  type: project
tags: [install.sh, claude-md, managed-block]
---

# repo CLAUDE.md is the managed-block source

`assemble_ccenv_base_claude_md()` in install.sh (runs FIRST, before components)
rebuilds the `# [CCENV MANAGED]` region of `~/.claude/CLAUDE.md` on every
install: marker header → verbatim `cat "$SCRIPT_DIR/CLAUDE.md"` → overlay
blocks → closing marker. Content outside the markers (e.g. `[AWARENESS
PROTOCOL]`) is preserved; the region itself is fully replaced.

Consequences:

- An edit made directly inside the managed region of `~/.claude/CLAUDE.md`
  survives only until the next install run. Any standing-order/policy change
  must ALSO be applied to `/src/ccenv/CLAUDE.md` (same file doubles as this
  repo's own project instructions).
- Keep the two byte-identical: install.sh `cmp`s the assembled result against
  the installed file and no-ops when equal. Verify with: extract the region
  between markers, drop the 2 header-comment lines, diff against the repo file.

Precedent (v0.13.3): the temp-file rule ("test scripts → project `tests/`,
/tmp only for true one-shots") was edited in place in `~/.claude/CLAUDE.md`
in one session; a later session back-ported it to the repo CLAUDE.md, bumped
bundle VERSION 0.13.2→0.13.3, added CHANGELOG + docs/install.md entries.

Related memory: `install-claude-md-component-owned` (top-level owns only the
BASE block; components append their own sections).
