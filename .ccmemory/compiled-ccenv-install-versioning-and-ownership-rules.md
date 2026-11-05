---
name: compiled-ccenv-install-versioning-and-ownership-rules
description: ccenv user corrections: bump once before first change (bundle too), installs never write source, pip --user not venvs, CLAUDE.md ownership, single ve…
metadata:
  type: feedback
tags: [compiled, ccenv, versioning, install, claude-md, git]
---

Central topic: how ccenv is versioned, installed and documented, and the user's corrections on each, plus the absolute no-git rule.

## Version bumps

`bump-top-level-bundle-version-not-just-subdir`
- **When:** only when a change to project source is about to be made, immediately before the first one in the session. A session changing nothing bumps nothing; a write to `.ccmemory/` is not a source change. Never a second bump in that session; later changes join the open version. The number moves again only to raise its size (patch -> minor -> major), never to add a step. Exception: a release seen during the session (install or push) closes that version and the next change opens a new one. Example: 0.36.0 was installed mid-session, so the installer fix after it shipped as 0.36.1.
- **What:** touching a component under `/src/ccenv` (ccloop/, ccmemory/, ccusage/, ccteam/ etc.): the per-component bump is necessary but NOT sufficient. Bump the component's `pyproject.toml`, `/src/ccenv/VERSION`, and `/src/ccenv/CHANGELOG.md` (one entry per session version, citing the component version, e.g. "ccloop v0.5.1: ...").
- **Why bundle:** the user installs the bundle, not subdirs; a component-only bump leaves what ships unversioned. User corrected repeatedly ("you keep doing this").
- **Why once, up front:** the note used to say "after editing any component, do BOTH bumps in the same change"; with the old CLAUDE.md line "the version is revved as part of the change" that produced seven bumps in one bug-fixing session before any release. Bumping at release was rejected because Claude cannot see a release, so an outside install would ship changed code under an already-released number. Decided 2026-09-19. Relates to `ccenv-installed-vs-source-version`.

## Installs never write to the source tree

`install-never-writes-to-source-tree`
- Never design `install.sh` (ccenv's or any project's) to write into the source tree, e.g. a `RELEASED` marker next to `VERSION`. User: "NO NO AND NO! app user or NO user should be able 'write' to this dir!! ... remember some users git clone trader then ./install.sh".
- User codes as `steve`, installs as the application user. `~/.config/ccenv/installed-version` is in the installing user's home, invisible to a `steve` session. Other users install from their own clone. So Claude cannot detect a release; anything "at release" must happen before it, hence the once-per-session bump.

## Single source of truth for version strings

`version-must-have-one-source-of-truth`
- Failure: `__version__` hardcoded in each `__init__.py` while `pyproject.toml` held the real number; both drifted two minor versions. `--version` printed ccloop 0.10.1 vs installed 0.12.0; ccmemory 0.15.0 vs 0.17.0. Caught only by running `ccloop --version` to confirm an install. A stale version reads as "the install failed" and sends you debugging a deploy that succeeded.
- Rule: never hand-maintain a version string; derive it:
```python
from importlib.metadata import PackageNotFoundError, version as _dist_version
try:
    __version__ = _dist_version("<pkg>")
except PackageNotFoundError:
    __version__ = "0+unknown"
```
  `importlib.metadata` is stdlib from 3.8, fine under ccenvmcp's 3.9 floor, no dependency. Applied to ccloop, ccmemory and ccenvmcp in v0.20.1 (ccenvmcp had not drifted; fixed anyway: the defect is the duplicated source, not the number).
- `--version` now reports what is installed, not the source tree; they differ until reinstall (see `ccenv-installed-vs-source-version`).
- Tautological test: `from ccloop import __version__; assert __version__ in capsys.readouterr().out` passes at any drift. A test whose expected value derives from the thing under test verifies plumbing, not correctness. Replaced with: `__init__.py` must not hardcode a version (regex on source), and `__version__ == importlib.metadata.version(pkg)`.
- Generalization: same class as the ccmemory listing budget (estimator drifted from its serializer) and ccloop handoff staleness. Any value hand-maintained in a second place drifts silently; audit by asking "how many places carry this number".

## Version history belongs in CHANGELOG.md

`no-version-history-in-claude-md`
- CLAUDE.md is purpose/architecture/conventions, never changelog. Trigger: `ccmemory/CLAUDE.md` had a 90-line "Architecture history" section covering v0.1.0 to v0.9.0. User: "why is history being kept in CLAUDE.md in _any_ project versus CHANGELOG.md???" and directed all module-level CLAUDE.md files cleared and removed (architecture already duplicated in README.md; only unique content was the changelog).
- Why: CLAUDE.md loads every session; history is pure overhead, release work can Read `CHANGELOG.md` on demand.
- Apply: never add "## Architecture history" / "Version history" / "Changelog" sections to any CLAUDE.md. Release entries go in the component's `CHANGELOG.md` (`## vX.Y.Z`, newest on top). Per-module CLAUDE.md files are deprecated; top-level `/src/ccenv/CLAUDE.md` is the global-rules file installed to `~/.claude/CLAUDE.md`; subdir architecture/install/test info goes in that subdir's `README.md`. About to write "this version did X, next added Y" outside CHANGELOG.md or a commit message: stop. Related: `ccenv-installed-vs-source-version`.

## pip --user, not per-component venvs

`no-per-component-venvs`
- Do not recommend a venv per component (old ccloop README: `python3 -m venv ~/.venvs/ccloop && pip install .` plus PATH symlink). User: "we'd have a venv for EACH component of this repo?"
- Standard: `pip3 install --user .`; `pipx install .` is the PEP 668 fallback for a manual single-component install only. Top-level install.sh installs all five components with `pip install --user`.
- Why: 5x disk for shared deps (mcp, anyio, pydantic), five symlink chains, inconsistent layouts, no needed isolation (same user, same `claude` CLI).
- install.sh does NOT fall back to pipx (v0.4.1): all five go into ONE shared `--user` site so the `ccenvmcp` shim imports across ccmemory/ccusage/ccteam; pipx venvs would break that. On PEP 668 (`error: externally-managed-environment`, `set -e` aborts) install.sh probes for the `EXTERNALLY-MANAGED` marker (`sysconfig.get_path("stdlib")`) and only when present AND pip supports it exports `PIP_BREAK_SYSTEM_PACKAGES=1` for its subshell. Safe: everything is `--user` into `~/.local`. Manual unblock: `PIP_BREAK_SYSTEM_PACKAGES=1 ./install.sh`. See `shared-userbase-compiled-dep-abi-mismatch`, `pythonuserbase-in-zshenv`. Never write per-component venv recipes; do not convert install.sh to pipx.

## Global CLAUDE.md ownership

`install-claude-md-component-owned` (set 2026-06-13, user's explicit direction)
- Top-level `install.sh` assembles ONLY the base (bundled `CLAUDE.md` plus user/system overlay blocks), FIRST, before any component installer (`assemble_ccenv_base_claude_md`).
- Base lives inside `# [CCENV MANAGED]` ... `# [/CCENV MANAGED]`; on re-run only that region is refreshed (awk strips old block, regenerates, preserves the rest): idempotent, no backup churn.
- Each component owns and appends its OWN section after the region (e.g. `ccproject/install.sh` step 4 manages its `# [AWARENESS PROTOCOL]` marker from its own `global-claude-md-snippet.md`). Top-level never reaches into a component's CLAUDE.md content.
- `CCPROJECT_SKIP_GLOBAL_CLAUDE_MD` was REMOVED; never reintroduce it or any skip coupling.
- Why: old design pulled `ccproject/global-claude-md-snippet.md`, ran ccproject early with a SKIP gate and assembled the global file last (~line 472) while a consumer ran at ~293, causing a false "Global CLAUDE.md missing awareness protocol" verify failure. User: "submodules can install/update global CLAUDE.md as they wish ... installer should not install submodule CLAUDE.md changes."
- Apply: new component needing global content gets its own marker-delimited idempotent self-install in its own installer; never re-centralize. Load-bearing structure, same spirit as `mcp-heal-stale-command-pattern`. Related: `no-per-component-venvs`.

## No git without direction

`no-git-checkout-to-undo-own-edits`
- The CLAUDE.md git ban is absolute across all subcommands including read-only (`git status`, `diff`, `log`, `reflog`). If about to run `git`, STOP and ask.
- Violation 1: undoing own edits with `git checkout -- <file>` / `git restore`. Use Edit/Write to restore; if prior content is not in context, ASK. The tree is shared state; "it only affected my changes" is luck.
- Violation 2: "read-only verification" with git. Use Read / find / wc / grep. To know what's uncommitted, ask.
- Why: git is too blunt and side-effecting (index state, pagers, hooks, lockfiles); the bright line makes the ban safe to rely on, and any carve-out invites the next ambiguous case.
- Apply: every `git ...` needs explicit permission from the user's words ("commit and push" allows the commit pipeline; "what's the git status" allows `git status`). When in doubt, ask first, run never.
