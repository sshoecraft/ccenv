---
name: bump-top-level-bundle-version-not-just-subdir
description: ccenv: one version per piece of work (not per session, not per defect), bumped before its first change — top-level bundle VERSION + CHANGELOG.md too.
metadata:
  type: feedback
tags: [ccenv, versioning, changelog, release]
---

**When:** one version per piece of work (bundle v0.38.2) — "mpath", "DRBD", "the installer" — never per defect, fix or test failure found while doing it. Before the first source change in a session, read the top `CHANGELOG.md` entry; it names the open version and the work. A change that is part of that work joins that version, even in a new session and even when it fixes a newly found bug. Only a change for different work bumps, immediately before it is made, and the entry is written at the bump, naming the work, not a defect. A write to `.ccmemory/` is not a source change. The number moves within an open version only to raise its size (patch → minor → major), never to add a step. A commit, push or install of the open version closes it, and so does the owner saying so; the next change opens a new one. Example: 0.36.0 was installed partway through a session, so the installer fix that came after it shipped as 0.36.1.

**What:** when the session touches a component under `/src/ccenv` (ccloop/, ccmemory/, ccusage/, ccteam/, etc.), the per-component bump is necessary but NOT sufficient. The installable artifact is the top-level `/src/ccenv` bundle. Bump:
- the component's `pyproject.toml`,
- `/src/ccenv/VERSION`, and
- `/src/ccenv/CHANGELOG.md` — one entry for the session's version, citing the component version (e.g. "ccloop v0.5.1: …").

**Why the bundle bump:** the user installs the ccenv bundle, not individual subdirs. A component bump alone leaves the thing that actually ships unversioned. The user corrected this repeatedly ("you keep doing this").

**Why per entry, up front:** this note used to say "after editing any component, do BOTH bumps in the same change." Together with the old CLAUDE.md line "the version is revved as part of the change," that produced seven bumps in one bug-fixing session before any release. The 2026-09-19 replacement, once per session, then bumped once per ccloop context window on a single problem: 0.90.31 became 0.90.122 in twelve days. A commit-based boundary was rejected because some projects have never been committed; the CHANGELOG entry works everywhere. v0.38.1 then said to name the entry for "the problem": an mxfs session named it for one hang, found a second hang during the same 16-node mpath testing, and opened 0.90.124 beside 0.90.123. User: "You're only supposed to bump the version one time for each thing you're working on ... Even if you use 300 sessions, that's one version." Making the owner close each version explicitly was rejected: "We want less hand holding instead of more." Bumping at the release instead was considered and rejected: Claude cannot see a release (see [[install-never-writes-to-source-tree]]), so an install outside Claude would ship changed code under an already-released number. Bumping before the first change means the number is new before any install can happen.

Relates to [[ccenv-installed-vs-source-version]].
