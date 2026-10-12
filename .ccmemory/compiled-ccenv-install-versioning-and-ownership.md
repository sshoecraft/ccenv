---
name: compiled-ccenv-install-versioning-and-ownership
description: ccenv install/versioning rules: one version per piece of work (not per session or defect) at bundle level, installer never writes source, no venvs, component-owned CLAUDE.md, no git.
metadata:
  type: feedback
tags: [compiled, ccenv, versioning, install, git]
---

Corrections about how ccenv is versioned, installed and edited. Topic group; the git note is the loosest fit and is included because it is a standing user correction.

## Versioning

- One source of truth per version number ([[version-must-have-one-source-of-truth]]). `__version__` was hardcoded in `__init__.py` while `pyproject.toml` held the real number; ccloop drifted 0.10.1 vs 0.12.0 and ccmemory 0.15.0 vs 0.17.0. A stale `--version` reads as "the install failed" and sends you debugging a deploy that succeeded. Derive from `importlib.metadata.version(pkg)` with a `PackageNotFoundError` fallback of `"0+unknown"` (stdlib, safe under the 3.9 floor). Applied to ccloop, ccmemory and ccenvmcp even though the last had not drifted: the defect is the duplicated source, not the current value. `--version` now reports what is installed, not what is in the tree.
- A test whose expected value derives from the thing under test (`assert __version__ in output`) is tautological and passes at any drift. Replace with: source must not hardcode a version (regex), and `__version__ == importlib.metadata.version(pkg)`. General audit question: how many places carry this number.
- Bump the bundle, not just the component ([[bump-top-level-bundle-version-not-just-subdir]]). The user installs the top-level bundle. When a component under `/src/ccenv` changes, bump the component `pyproject.toml`, `/src/ccenv/VERSION`, and add one `/src/ccenv/CHANGELOG.md` entry citing the component version. The user corrected the omission repeatedly.
- One version per piece of work — "mpath", "DRBD", "the installer" — not per session and not per defect found during it (bundle v0.38.2). Before the first source change, read the top `CHANGELOG.md` entry, which names the open version and the work: a change that is part of that work joins it, including fixes for newly found bugs; only different work bumps, and the entry is written at the bump naming the work. Commit, push, install or the owner saying so closes the open version. Writes to `.ccmemory/` never bump; the number moves within an open version only to raise size. History: "bump after every edit" gave seven bumps in one session; "once per session" gave one bump per ccloop context window (0.90.31 → 0.90.122 in twelve days); "once per entry, naming the problem" let an entry be named for one hang, so a second hang in the same mpath testing opened 0.90.124. A commit-based boundary was rejected because some projects have never been committed; owner-only closing was rejected as hand-holding. Bumping at release time was rejected because Claude cannot observe a release.

## Installer constraints

- `install.sh` must never write into the source tree, e.g. a `RELEASED` marker beside `VERSION` ([[install-never-writes-to-source-tree]]). User: "NO NO AND NO!" Installs run as the application user, or by other users from their own `git clone`. `~/.config/ccenv/installed-version` lives in the installer's home, invisible to a session running as `steve`. Hence anything needed "at release" must happen before it, which is why the bump is up front.
- No venv per component ([[no-per-component-venvs]]). Manual single-component install: `pip3 install --user .`, `pipx install .` as PEP 668 fallback. The bundle `install.sh` puts all five components in one shared `--user` site so the `ccenvmcp` shim imports across ccmemory/ccusage/ccteam; pipx per-app venvs would break that, so do not convert it. On PEP 668 distros it detects the `EXTERNALLY-MANAGED` marker and exports `PIP_BREAK_SYSTEM_PACKAGES=1` (safe: everything lands in `~/.local`). Manual unblock: `PIP_BREAK_SYSTEM_PACKAGES=1 ./install.sh`. Never write per-component venv recipes.
- `~/.claude/CLAUDE.md` ownership ([[install-claude-md-component-owned]]). Top-level `install.sh` assembles only the base inside `# [CCENV MANAGED]` ... `# [/CCENV MANAGED]`, first, before any component installer (`assemble_ccenv_base_claude_md`), refreshing only that region on re-run. Each component installer appends its own marker-delimited idempotent section. The top level never reaches into component content. `CCPROJECT_SKIP_GLOBAL_CLAUDE_MD` was removed; do not reintroduce a skip gate. The old inverted ordering caused a false "missing awareness protocol" verify failure.

## Git

- Never run `git` without explicit direction, including read-only `status`, `diff`, `log` ([[no-git-checkout-to-undo-own-edits]]). To undo your own edit, use Edit/Write to restore content, or ask if you lack the prior text; `checkout`/`restore` can wipe the user's uncommitted work too. Verify file state with Read, grep, wc, find. Permission must come from the user's words (e.g. "commit and push"), not inference.
