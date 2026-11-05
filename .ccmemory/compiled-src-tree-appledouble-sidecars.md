---
name: compiled-src-tree-appledouble-sidecars
description: /src sits on an xattr-less FS that spawns macOS ._* AppleDouble sidecars, corrupting pip wheels; build from a clean /tmp stage.
metadata:
  type: project
tags: [compiled, appledouble, pip, wheel, nfs, install]
---

Backfill part for [[compiled-python-userbase-install-pitfalls]], which cites this source without its facts.

## AppleDouble sidecars on /src
Source: [[src-tree-appledouble-sidecars]]
- `/src` (-> `/System/Volumes/Data/src`) is on a filesystem that cannot store macOS extended attributes natively, so the OS materializes AppleDouble `._*` sidecar files next to anything written. `COPYFILE_DISABLE=1` does NOT prevent this: it governs only `tar`/`cp`, not FS-level creation.
- Symptom: `pip install` of a local package fails with `invalid wheel, multiple .dist-info directories found: ._<pkg>.dist-info, <pkg>.dist-info`. During `bdist_wheel` setuptools writes `<pkg>.dist-info/`, the FS drops `._<pkg>.dist-info` beside it, and the zip step sweeps both into the wheel.
- Fix in install.sh `pip_install_local`: stage the source to `/tmp` (native APFS, no sidecars), excluding `._*`/`build`/`dist`/`*.egg-info`/`__pycache__`, and build/install from the clean copy. All local pip installs (ccmemory/ccloop/ccteam/overlays) route through it.
- Also affects ccmemory stores: `.ccmemory/` dirs accumulate `._*.md` sidecars that the index scan picked up as junk. ccmemory v0.6.1 `paths.ensure_gitignore()` writes `._*` into each store's `.gitignore` (out of git, cannot stop them on disk).
- Broader cleanup NOT done (needs user + git): a global `core.excludesfile` with `._*` would keep all source-tree sidecars (`._README.md`, `._direct.js`, etc.) out of every repo.
- Related: `pythonuserbase-in-zshenv`.
