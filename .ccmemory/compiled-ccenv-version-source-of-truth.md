---
name: compiled-ccenv-version-source-of-truth
description: ccenv version handling: installed-vs-source marker, importlib.metadata for __version__, top-level bundle bump, CHANGELOG-only history.
metadata:
  type: project
tags: [compiled, versioning, ccenv, install, changelog]
---

## Topic

ccenv has one recurring failure class: a version or history value gets hand-maintained in more than one place, drifts, and the drift is silent until someone hits it in production. Four incidents, same root cause, different location in the bundle.

## 1. Source VERSION vs installed-version marker ([[ccenv-installed-vs-source-version]])

`/src/ccenv/VERSION` (source tree) and `~/.config/ccenv/installed-version` (written by `install.sh` on success) answer different questions and must not be conflated.

`/src` is NFS-shared across clyde / serv / solardirector / infra / mac-mini / trader@*. A `git pull` or NFS write updates the source tree instantly on every machine sharing it — but only `./install.sh` updates a given machine's installed bits (pip packages, hooks, `~/.local/bin/...`). First cross-machine bump (0.1.0→0.1.1): clyde pushed the VERSION file, all six other machines instantly read 0.1.1 off shared NFS, `instenv.prompt` reported every one of them "current," and none had actually run `install.sh` — their installed bits were still 0.1.0.

Rule: any "is this machine's ccenv current?" check reads `~/.config/ccenv/installed-version`, never `/src/ccenv/VERSION`. `install.sh` writes that marker at the very end of a successful run (after `set -e` would already have aborted on failure), so it's truthful by construction; missing/empty means "never installed, needs install." Do not use `pip show <component>` (component versions are independent of the bundle version) or commit SHA comparisons (SHAs move on every commit, including ones that don't bump VERSION) as a substitute.

## 2. Per-component version bump is necessary but not sufficient ([[bump-top-level-bundle-version-not-just-subdir]])

Fixing a component under `/src/ccenv` (ccloop/, ccmemory/, ccusage/, ccteam/) and bumping only that component's `pyproject.toml`/`__init__.py` leaves the thing that actually ships — the top-level `/src/ccenv` bundle — unversioned, so the fix is invisible to the installer. User called this out repeatedly ("you keep doing this").

Rule: the version is bumped once per session, before the first change, and never again in that session. It moves again only to raise its size (patch → minor → major). A session that touches a component bumps BOTH the component version AND `/src/ccenv/VERSION`, plus one `/src/ccenv/CHANGELOG.md` entry for that version that cites the component version (e.g. "ccloop v0.5.1: …").

This used to say "in the same change." Together with the old CLAUDE.md line "the version is revved as part of the change," that produced seven bumps in one bug-fixing session before any release existed. Bumping at release time was rejected because Claude cannot observe a release: installs run as another user or from another clone, and no install may write to the source tree ([[install-never-writes-to-source-tree]]). Decided 2026-09-19.

## 3. `__version__` must be derived, never hardcoded ([[version-must-have-one-source-of-truth]])

Both ccloop and ccmemory hardcoded `__version__` in `__init__.py` while `pyproject.toml` carried the real number; nobody remembered to bump both, and both drifted two minor versions (`--version` printed 0.10.1/0.15.0 while the installed dist was actually 0.12.0/0.17.0). Caught only because someone ran `--version` to confirm an install had taken — a stale version reads as "the install failed," which is the worst moment for the number to be wrong.

Fix, applied to ccloop, ccmemory AND ccenvmcp in v0.20.1 (ccenvmcp hadn't even drifted yet — fixed anyway, because the defect is the duplicated source of truth, not the current number):

```python
from importlib.metadata import PackageNotFoundError, version as _dist_version
try:
    __version__ = _dist_version("<pkg>")
except PackageNotFoundError:
    __version__ = "0+unknown"
```

stdlib since 3.8, holds under ccenvmcp's 3.9 floor, no new dependency. Consequence: `--version` now reports what's *installed*, not what's in the source tree — they differ until reinstall, which is the same installed-vs-source split as item 1, just at the component level instead of the bundle level.

The test that failed to catch the original drift was tautological — it imported `__version__` and asserted the CLI printed that same constant, so it passed at any drift. Replaced with: `__init__.py` must not hardcode a version (regex over source) AND `__version__` must equal `importlib.metadata.version(pkg)`. General lesson, same class as the ccmemory listing-budget estimator drift and the ccloop handoff-doc staleness: any value hand-maintained in a second place will drift, and the drift is silent by construction — audit by asking "how many places carry this number," not "is this number currently right."

## 4. Version/release history belongs in CHANGELOG.md, never CLAUDE.md ([[no-version-history-in-claude-md]])

`ccmemory/CLAUDE.md` had accumulated a 90-line "Architecture history" section listing every release v0.1.0→v0.9.0 with paragraphs per version. User: "why is history being kept in CLAUDE.md in *any* project versus CHANGELOG.md???" — directed all module-level CLAUDE.md files cleared of history and removed entirely (their unique content was only the changelog; architecture content already duplicated into README.md).

Why it matters specifically for ccenv: CLAUDE.md loads into every session's context at start. Version-history paragraphs are pure overhead paid by every session, for the benefit of the rare session doing release-management work — and that session can just Read CHANGELOG.md on demand.

Rule: never add "## Architecture history" / "## Version history" / "## Changelog" to any CLAUDE.md, top-level or per-module. Release entries go in that component's `CHANGELOG.md` (`## vX.Y.Z`, newest first; create one if absent). Per-module CLAUDE.md files in this repo are deprecated entirely — the top-level `/src/ccenv/CLAUDE.md` is the global-rules file installed to `~/.claude/CLAUDE.md`; subdirectory install/test/architecture info belongs in that subdirectory's README.md. Same "single source of truth per concern" family as items 1-3.

## Net

Four places in ccenv where a version-shaped fact can live twice: bundle-vs-installed marker, bundle-vs-component VERSION, hardcoded-vs-derived `__version__`, and history-in-CLAUDE.md-vs-CHANGELOG. All four were fixed by picking one authoritative location and deriving or reading from it everywhere else, never hand-copying.
