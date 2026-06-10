---
name: src-tree-appledouble-sidecars
description: /src tree is on an xattr-less FS that spawns macOS ._* AppleDouble sidecars on every write; corrupts pip wheels — build from clean /tmp stage
metadata:
  type: reference
tags: [appledouble, filesystem, pip, wheel, macos, nfs, build]
---

The `/src` tree on this machine (`/src` → `/System/Volumes/Data/src`) lives on a filesystem that **cannot store macOS extended attributes natively**, so the OS materializes AppleDouble `._*` sidecar files next to anything written there. `COPYFILE_DISABLE=1` does NOT prevent this — that only governs `tar`/`cp`, not FS-level sidecar creation.

**Symptom that bit us:** `pip install` of a local package failed with `invalid wheel, multiple .dist-info directories found: ._<pkg>.dist-info, <pkg>.dist-info`. During `bdist_wheel`, setuptools writes `<pkg>.dist-info/`, the FS drops a `._<pkg>.dist-info` sidecar beside it, and the zip step sweeps both into the wheel.

**Fix (in install.sh `pip_install_local`):** stage the source to `/tmp` (native APFS, no sidecars) excluding `._*`/`build`/`dist`/`*.egg-info`/`__pycache__`, then build/install from the clean copy. All local pip installs (ccmemory/ccloop/ccteam/overlays) route through it.

**Also affects ccmemory stores:** `.ccmemory/` dirs accumulate `._*.md` sidecars that the index scan picked up as junk. ccmemory v0.6.1's `paths.ensure_gitignore()` writes `._*` into each store's `.gitignore` so they stay out of git (can't stop them on disk, only out of commits).

**Broader cleanup (NOT done — needs user + git):** a global `core.excludesfile` with `._*` would keep all source-tree sidecars (`._README.md`, `._direct.js`, etc.) out of every repo at once.

Related: [[pythonuserbase-in-zshenv]]</body>
