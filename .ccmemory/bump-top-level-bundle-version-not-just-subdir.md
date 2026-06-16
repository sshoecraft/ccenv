---
name: bump-top-level-bundle-version-not-just-subdir
description: When fixing a component in /src/ccenv, bump the TOP-LEVEL bundle VERSION + CHANGELOG.md — the bundle is what installs, not the component subdir.
metadata:
  type: feedback
tags: [ccenv, versioning, changelog, workflow]
---

When making a change to any component under `/src/ccenv` (ccloop/, ccmemory/, ccusage/, ccteam/, etc.), the per-component version bump is necessary but NOT sufficient.

**The installable artifact is the top-level `/src/ccenv` bundle.** Always also:
- bump `/src/ccenv/VERSION` (patch=fix, minor=feature, major=breaking), and
- prepend an entry to `/src/ccenv/CHANGELOG.md` describing the change, citing the component version (e.g. "ccloop v0.5.1: …").

**Why:** the user installs the ccenv bundle, not individual subdirs. A component bump alone leaves the thing that actually ships unversioned, so the fix is invisible to the installer. The user has corrected this repeatedly ("you keep doing this") — do not `cd` into a subdir, fix it, bump only its version, and stop. Go up to the bundle root and version/changelog it there too.

**How to apply:** after editing any component, do BOTH bumps in the same change: component `pyproject.toml`/`__init__.py` AND top-level `VERSION`+`CHANGELOG.md`. Relates to [[ccenv-installed-vs-source-version]].
