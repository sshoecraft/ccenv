---
name: shared-userbase-compiled-dep-abi-mismatch
description: PYTHONUSERBASE shares ONE version-agnostic site-packages across pythons; a python bump strands stale-ABI .so files. install.sh v0.1.5 auto-heals via…
metadata:
  type: project
---

`$PYTHONUSERBASE=~/.local` + Homebrew's `osx_framework_user` scheme collapses the `--user` site to a **single version-agnostic** `lib/python/site-packages` shared by every interpreter (3.9, 3.13, 3.14...). Verify: `python3 -c 'import site;print(site.getusersitepackages())'` returns the SAME path under 3.13 and 3.14. Pure-Python deps survive cross-version; **compiled extensions are ABI-tagged** (`*.cpython-39-darwin.so` vs `*.cpython-314-darwin.so`) and load only under their matching interpreter.

**Failure seen 2026-06-13:** ccteam MCP `✘ Failed to connect`. Launcher runs Python 3.14 but the shared dir held only `watchfiles/_rust_notify.cpython-39-darwin.so` (a 3.9 build) → `ModuleNotFoundError: No module named 'watchfiles._rust_notify'`. ccmemory/ccusage were fine — stdlib-only (ccenvmcp shim, see [[ccenvmcp-stdlib-mcp-shim]]); ccteam is the only component with a compiled dep. A scan found **12 stale cp39 `.so` across 7 dists** (watchfiles, cffi, websockets, SQLAlchemy, uvloop, httptools, PyYAML, pydantic_core) — the whole shared site was stranded by the 3.9→3.14 bump, not just ccteam. Pip never refetched the right-ABI wheels because it saw the dists already "present."

**Manual fix (one dist):** `python3.14 -m pip install --user --force-reinstall --no-deps watchfiles`.

**Automated fix — IMPLEMENTED in install.sh v0.1.5** (`heal_stale_compiled_exts()`, runs after all components install): walks the shared user-site for `.so`/`.pyd`/`.dylib` whose ABI tag ≠ the running interpreter's `EXT_SUFFIX` (skips `.abi3.so` + untagged), maps each stale file to its owning pip distribution via that dist's `RECORD`, and force-reinstalls the **exact** version (`name==version`, `--force-reinstall --no-deps`, **no** `--upgrade`) so it rebuilds the same release for the new ABI without surprise-upgrading packages ccenv doesn't own. Generic, self-heals an already-broken box (keys off on-disk `.so`, not a marker). Also writes `~/.config/ccenv/python-tag` (`sys.implementation.cache_tag`) so the next run can announce a bump. Docs: `docs/install.md`.

**How to apply:** Re-running `./install.sh` now auto-heals stale-ABI native deps. If hand-fixing, force-reinstall the exact version pinned. Related: [[pythonuserbase-in-zshenv]], [[no-per-component-venvs]], [[ccenv-installed-vs-source-version]].
