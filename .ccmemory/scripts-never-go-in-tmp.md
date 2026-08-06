---
name: scripts-never-go-in-tmp
description: Temp-file rule splits on KIND not lifetime: every script goes in the repo (tests/ or tools/scripts/); /tmp is for data only. ccenv v0.18.1.
metadata:
  type: feedback
tags: [claude-md, temp-files, policy]
---

# Scripts never go in /tmp — the split is by kind, not lifetime

The v0.13.3 wording ("anything that *might be used again* → `tests/`, truly
temporary files → /tmp") failed in practice. It required predicting whether a
script would be rerun, and that prediction was reliably wrong: every script
arrived with a story about why *this* one was throwaway, so scripts of all
kinds kept landing in /tmp and getting wiped on reboot.

v0.18.1 removes the prediction from the rule:

- **Scripts are categorically not temp files.** Test harness, debug probe,
  repro case, one-off migration, data-munger, convenience helper — all go in
  the repo.
  - harnesses/probes/repros → project `tests/`
  - utilities, ops/build helpers → project `tools/` or `scripts/` (whichever
    the project already has; create `scripts/` if neither)
- **/tmp is for data only** — scratch output, downloaded blobs, throwaway
  fixtures, intermediate dumps.

Note the standing tension: the Claude Code session harness supplies a
scratchpad under `/tmp/claude-<uid>/…/scratchpad` and instructs that all temp
files go there. CLAUDE.md overrides that for anything executable. The scratchpad
is only for single-use data.

Still compatible with `test-destructive-scripts-against-fixtures-only`: the
destructive-test *fixture tree* is data and still belongs in /tmp; the script
that operates on it does not.

Landed in `/src/ccenv/CLAUDE.md` (the verbatim `[CCENV MANAGED]` source) and
mirrored byte-identically into `~/.claude/CLAUDE.md` so it applies before the
next install; VERSION 0.18.0→0.18.1, CHANGELOG + `docs/install.md` History.
See `repo-claude-md-is-managed-block-source`.
