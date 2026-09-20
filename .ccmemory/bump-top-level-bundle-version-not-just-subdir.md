---
name: bump-top-level-bundle-version-not-just-subdir
description: ccenv: bump ONCE per session, before the first change — top-level bundle VERSION + CHANGELOG.md too, not just the component subdir.
metadata:
  type: feedback
tags: [ccenv, versioning, changelog, release]
---

**When:** only when a change to the project's source is about to be made — immediately before the first one in the session. A session that changes nothing bumps nothing, and a write to `.ccmemory/` is not a source change. Never a second time in that session — later changes join the version already opened. The number moves again only to raise its size (patch → minor → major), never to add a step. Exception: a release seen during the session (an install or push run inside it) closes that version, and the next change opens a new one. That happened in the session that set this up: 0.36.0 was installed partway through, so the installer fix that came after it shipped as 0.36.1.

**What:** when the session touches a component under `/src/ccenv` (ccloop/, ccmemory/, ccusage/, ccteam/, etc.), the per-component bump is necessary but NOT sufficient. The installable artifact is the top-level `/src/ccenv` bundle. Bump:
- the component's `pyproject.toml`,
- `/src/ccenv/VERSION`, and
- `/src/ccenv/CHANGELOG.md` — one entry for the session's version, citing the component version (e.g. "ccloop v0.5.1: …").

**Why the bundle bump:** the user installs the ccenv bundle, not individual subdirs. A component bump alone leaves the thing that actually ships unversioned. The user corrected this repeatedly ("you keep doing this").

**Why once per session, up front:** this note used to say "after editing any component, do BOTH bumps in the same change." Together with the old CLAUDE.md line "the version is revved as part of the change," that produced seven bumps in one bug-fixing session before any release. Bumping at the release instead was considered and rejected: Claude cannot see a release (see [[install-never-writes-to-source-tree]]), so an install outside Claude would ship changed code under an already-released number. Bumping before the first change means the number is new before any install can happen. Decided 2026-09-19.

Relates to [[ccenv-installed-vs-source-version]].
