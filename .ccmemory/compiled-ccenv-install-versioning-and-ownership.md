---
name: compiled-ccenv-install-versioning-and-ownership
description: ccenv versioning, installer ownership, pip/venv policy, CLAUDE.md hygiene, single-source version, and the no-git rule for undoing edits.
metadata:
  type: feedback
tags: [compiled, ccenv, versioning, install, git, claude-md]
---

# ccenv install, versioning and ownership feedback

## Version bump policy
- **When:** only when a source change is about to be made — immediately before the first one in the session. A session that changes nothing bumps nothing; a write to `.ccmemory/` is not a source change. Never bump a second time in a session; later changes join the open version. The number moves again only to raise its size (patch -> minor -> major), never to add a step. Exception: a release seen during the session (install or push run inside it) closes that version; the next change opens a new one. 0.36.0 was installed partway through the session that set this up, so the later installer fix shipped as 0.36.1. `bump-top-level-bundle-version-not-just-subdir`
- **What:** when touching a component under `/src/ccenv` (ccloop/, ccmemory/, ccusage/, ccteam/ ...), the component bump is necessary but NOT sufficient. Bump the component `pyproject.toml`, `/src/ccenv/VERSION`, and `/src/ccenv/CHANGELOG.md` (one entry per session version citing the component version, e.g. "ccloop v0.5.1: ..."). The user installs the bundle, not subdirs; user corrected this repeatedly ("you keep doing this"). `bump-top-level-bundle-version-not-just-subdir`
- **Why once up front:** the old note said "do BOTH bumps after editing any component", plus the old CLAUDE.md line "the version is revved as part of the change", yielded seven bumps in one bug-fixing session before any release. Bumping at release was rejected because Claude cannot see a release; an install outside Claude would ship changed code under a released number. Decided 2026-09-19. Relates to `ccenv-installed-vs-source-version`. `bump-top-level-bundle-version-not-just-subdir`

## Installer never writes into the source tree
- Never design `install.sh` (ccenv's or any project's) to write into the source tree, e.g. a `RELEASED` marker next to `VERSION`. User: "NO NO AND NO! app user or NO user should be able 'write' to this dir!! ... remember some users git clone trader then ./install.sh". `install-never-writes-to-source-tree`
- Facts: user codes as `steve` and installs as the application user; `~/.config/ccenv/installed-version` is in the installing user's home, invisible to a `steve` session. Others install from their own clone. So Claude cannot detect a release; anything "at release" must happen before it, hence the bump-before-first-change rule. `install-never-writes-to-source-tree`

## Global CLAUDE.md ownership (set 2026-06-13 at user's direction)
- Top-level `install.sh` assembles ONLY the base (bundled `CLAUDE.md` plus user/system overlay blocks), FIRST, before any component installer (`assemble_ccenv_base_claude_md`). `install-claude-md-component-owned`
- Base lives inside `# [CCENV MANAGED]` ... `# [/CCENV MANAGED]`; re-runs refresh only that region (awk strips old block, regenerates, preserves the rest): idempotent, no backup churn. `install-claude-md-component-owned`
- Each component installer owns and appends its OWN section after the region (e.g. `ccproject/install.sh` step 4 manages its `# [AWARENESS PROTOCOL]` marker from its own `global-claude-md-snippet.md`). Top-level never reaches into component CLAUDE.md content. `install-claude-md-component-owned`
- `CCPROJECT_SKIP_GLOBAL_CLAUDE_MD` was REMOVED; never reintroduce it or any skip coupling. Old design pulled the ccproject snippet into top-level and assembled the global file last (line ~472) while ccproject ran at ~293: caused a false "Global CLAUDE.md missing awareness protocol" verify failure. User: "submodules can install/update global CLAUDE.md as they wish ... installer should not install submodule CLAUDE.md changes." New component needing global content: own marker-delimited idempotent self-install in its own installer. Load-bearing; don't "simplify". Same spirit as `mcp-heal-stale-command-pattern`; related `no-per-component-venvs`. `install-claude-md-component-owned`

## pip / venv policy
- Do NOT recommend a venv per component (old ccloop README: `python3 -m venv ~/.venvs/ccloop && pip install .` plus PATH symlink). User: "we'd have a venv for EACH component of this repo?" `no-per-component-venvs`
- Standard: `pip3 install --user .`, with `pipx install .` as PEP 668 fallback for a manual single-component install only. Top-level install.sh installs all five components with `pip install --user`. Reason: 5x disk for shared deps (mcp, anyio, pydantic), five symlink chains, inconsistent layouts, no needed isolation (same user, same `claude` CLI). `no-per-component-venvs`
- install.sh does NOT fall back to pipx (v0.4.1): all five components share ONE `--user` site so the `ccenvmcp` shim is importable across ccmemory/ccusage/ccteam; pipx venvs would break that. On PEP 668 (`error: externally-managed-environment`; `set -e` aborts) install.sh probes the `EXTERNALLY-MANAGED` marker (`sysconfig.get_path("stdlib")`) and, only when present AND pip supports it, exports `PIP_BREAK_SYSTEM_PACKAGES=1` for its subshell. Safe: everything goes to `~/.local`. Manual unblock: `PIP_BREAK_SYSTEM_PACKAGES=1 ./install.sh`. Never convert install.sh to pipx; never write per-component venv recipes. See `shared-userbase-compiled-dep-abi-mismatch`, `pythonuserbase-in-zshenv`. `no-per-component-venvs`

## No version history in CLAUDE.md
- CLAUDE.md = purpose, architecture, current conventions; changelog content goes in `CHANGELOG.md` beside `README.md`. Trigger: `ccmemory/CLAUDE.md` had a 90-line "Architecture history" section covering v0.1.0 to v0.9.0. User: "why is history being kept in CLAUDE.md in _any_ project versus CHANGELOG.md???" and directed all module-level CLAUDE.md files cleared and removed. `no-version-history-in-claude-md`
- Why: CLAUDE.md loads every session; history is overhead only release-management sessions need (they can Read `CHANGELOG.md`). `no-version-history-in-claude-md`
- Apply: never add "Architecture history"/"Version history"/"Changelog" sections to any CLAUDE.md. Releases get `CHANGELOG.md` entries (`## vX.Y.Z`, newest on top). Per-module CLAUDE.md deprecated; top-level `/src/ccenv/CLAUDE.md` is the global rules file installed to `~/.claude/CLAUDE.md`; subdir architecture/install/test info goes in that subdir's `README.md`. Stop before writing "this version did X, next added Y" anywhere but CHANGELOG.md or a commit message. Related: `ccenv-installed-vs-source-version`. `no-version-history-in-claude-md`

## One source of truth for `__version__`
- Failure: `__version__` hardcoded in `__init__.py` while `pyproject.toml` held the real number; both drifted two minor versions. `--version` printed ccloop 0.10.1 vs installed 0.12.0; ccmemory 0.15.0 vs 0.17.0. Caught only by running `ccloop --version` to confirm an install. A stale version reads as "the install failed" and sends you debugging a successful deploy. `version-must-have-one-source-of-truth`
- Rule: never hand-maintain a version string in `__init__.py`:
```python
from importlib.metadata import PackageNotFoundError, version as _dist_version
try:
    __version__ = _dist_version("<pkg>")
except PackageNotFoundError:
    __version__ = "0+unknown"
```
  stdlib from 3.8, fine under ccenvmcp's 3.9 floor, no dependency. Applied to ccloop, ccmemory, ccenvmcp in v0.20.1 (ccenvmcp hadn't drifted; fixed anyway: the defect is the duplicated source, not the current number). `--version` now reports what is installed, not the source tree; see `ccenv-installed-vs-source-version`. `version-must-have-one-source-of-truth`
- Tautological test: `from ccloop import __version__; assert __version__ in capsys.readouterr().out` passes at any drift. A test whose expected value derives from the thing under test verifies plumbing, not correctness. Replaced with: regex on source that `__init__.py` hardcodes no version, and `__version__ == importlib.metadata.version(pkg)`. `version-must-have-one-source-of-truth`
- Generalization: same class as the ccmemory listing budget (estimator drifted from the serializer it models) and ccloop handoff staleness (hand-maintained doc going stale). Any value hand-maintained in a second place drifts silently; audit by asking "how many places carry this number". `version-must-have-one-source-of-truth`

## git ban covers undoing own edits and read-only checks
- The CLAUDE.md git ban is absolute: every subcommand incl. `git status`, `git diff`, `git log`, `git reflog`. "If you find yourself about to run `git`, STOP and ask the user what they want instead." `no-git-checkout-to-undo-own-edits`
- Violation 1: undoing own edits with `git checkout -- <file>` / `git restore`. Use Edit/Write to restore; if prior content isn't in context, ASK. The tree is shared; "it only affected my changes" is luck. `no-git-checkout-to-undo-own-edits`
- Violation 2: "read-only verification". Use Read/find/wc/grep; if you need to know what's uncommitted, ASK. `no-git-checkout-to-undo-own-edits`
- Why: git is too blunt/side-effecting (index state, pagers, hooks, lockfiles); a bright line makes it safe to rely on. Implicit permission comes only from the user's words ("commit and push" allows the commit pipeline; "what's the git status" allows `git status`). When in doubt, ask first, run never. `no-git-checkout-to-undo-own-edits`
