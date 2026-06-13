---
name: ccmemory-debian-build-unknown-install-layout
description: FIX IMPLEMENTED (pending on-box verify): install.sh now bootstraps a PEP 621-capable toolchain (setuptools>=61) into --user before any pip build.
metadata:
  type: project
---

On Debian 11 / Raspberry Pi OS (`solardirector`, Python 3.9.2, pip 20.3.4, setuptools ~52) `./install.sh` failed building every PEP 621 package:

```
Building wheel for UNKNOWN (PEP 517) ... error
running bdist_wheel -> build -> install -> install_egg_info
AttributeError: install_layout
```

**Root cause:** the build ran under Debian's SYSTEM setuptools ~52 (build isolation effectively bypassed). setuptools <61 cannot parse a PEP 621 `[project]` table → name comes out `UNKNOWN` → it takes the legacy `install_egg_info` path → trips Debian's `install_layout` patch. NOT a packaging bug; clean with a modern pip. Independent of the `mcp`/3.10 issue — affects ALL packages in the repo (every one uses `[project]`), including the new `ccenvmcp`.

**FIX IMPLEMENTED 2026-06-13** in `install.sh`:
1. New `ensure_build_toolchain()` — detects setuptools major version via `python3 -c`; if <61, runs `python3 -m pip install --user --upgrade pip setuptools wheel` into ~/.local (PYTHONUSERBASE). No-op when setuptools is already recent; never touches the system interpreter apt depends on. Called once right after `assemble_ccenv_base_claude_md`, before any component install.
2. `pip_install_local()` switched from the `pip3` SCRIPT to `python3 -m pip` so the upgraded user-site pip/setuptools are actually used (the `pip3` entry-point stays pinned to old system pip; `python3 -m pip` picks up ~/.local which is ahead on sys.path).
   - ccusage/install.py already used `sys.executable -m pip`, so it inherits the fix automatically.

**STATUS:** implemented + locally consistent, but NOT yet verified on the actual Pi/Debian box. Next step: run `./install.sh` on `solardirector` and confirm it builds past the ccenvmcp/ccmemory steps. Related: [[ccenvmcp-stdlib-mcp-shim]], [[src-tree-appledouble-sidecars]], [[no-per-component-venvs]], [[pythonuserbase-in-zshenv]].
