---
name: compiled-ccenv-mcp-heal-and-abi-heal
description: install.sh healing patterns: register_mcp() re-registers stale commands; heal_stale_compiled_exts() fixes stale-ABI .so files in the shared user site.
metadata:
  type: feedback
tags: [compiled, install, mcp, abi, heal, ccenv]
---

Backfill part for [[compiled-ccenv-install-and-version-conventions]], which cites both sources without their facts.

## register_mcp() compare-and-heal: never simplify away
Source: [[mcp-heal-stale-command-pattern]]
- `register_mcp()` in install.sh: (1) `mcp_registered "$name"` checks existence; (2) if present, `mcp_current_command "$name"` parses `claude mcp get` output, gluing `Command:` and `Args:` lines into one string; (3) compares to `$*` (what would be registered now); (4) on difference prints "registered with stale command — re-registering", runs `claude mcp remove`, then `claude mcp add` with the new command.
- Without it, an MCP registered with a stale binary path (bare `ccmemory` from a pre-PYTHONUSERBASE install, or `~/Library/Python/3.14/bin/ccmemory mcp` after switching to `~/.local`) stays broken forever: `mcp_registered` returns true and the old skip-if-registered logic walked away.
- Same compare-and-heal lives in `ccusage/install.py:register_mcp_user()` (Python).
- Why: before it, install once with a wrong path -> entry pinned -> `claude mcp list` shows "Failed to connect" -> re-running install.sh did nothing -> manual `claude mcp remove ccmemory` plus rerun. It took an entire session to identify as the root cause of ccmemory/ccteam connection failures on macOS.
- Apply: register new MCPs only through `register_mcp()`, never inline `claude mcp add`; a component with its own installer (like ccusage's install.py) replicates the heal; never restore "leaving as-is".

## Shared user-site stale-ABI compiled deps
Source: [[shared-userbase-compiled-dep-abi-mismatch]]
- `PYTHONUSERBASE=~/.local` + Homebrew's `osx_framework_user` scheme collapses the `--user` site to ONE version-agnostic `lib/python/site-packages` shared by every interpreter (3.9, 3.13, 3.14). Verify: `python3 -c 'import site;print(site.getusersitepackages())'` gives the SAME path under 3.13 and 3.14. Pure-Python deps survive; compiled extensions are ABI-tagged (`*.cpython-39-darwin.so` vs `*.cpython-314-darwin.so`) and load only under the matching interpreter.
- Failure 2026-06-13: ccteam MCP "Failed to connect". Launcher ran Python 3.14 but the shared dir held only `watchfiles/_rust_notify.cpython-39-darwin.so` -> `ModuleNotFoundError: No module named 'watchfiles._rust_notify'`. ccmemory/ccusage fine (stdlib-only, `ccenvmcp-stdlib-mcp-shim`); ccteam is the only component with a compiled dep. Scan: 12 stale cp39 `.so` across 7 dists (watchfiles, cffi, websockets, SQLAlchemy, uvloop, httptools, PyYAML, pydantic_core); the whole site was stranded by the 3.9 -> 3.14 bump. Pip never refetched right-ABI wheels because dists looked present.
- Manual fix (one dist): `python3.14 -m pip install --user --force-reinstall --no-deps watchfiles`.
- Automated fix, install.sh v0.1.5 `heal_stale_compiled_exts()`, after all components install: walk the shared user-site for `.so`/`.pyd`/`.dylib` whose ABI tag differs from the running `EXT_SUFFIX` (skip `.abi3.so` and untagged), map each to its owning dist via `RECORD`, force-reinstall the EXACT `name==version` with `--force-reinstall --no-deps`, no `--upgrade` (rebuilds the same release for the new ABI without surprise-upgrading packages ccenv does not own). Generic, keys off on-disk `.so` not a marker, so it heals already-broken boxes. Writes `~/.config/ccenv/python-tag` (`sys.implementation.cache_tag`) so the next run can announce a bump. Docs `docs/install.md`.
- Apply: rerunning `./install.sh` auto-heals; hand-fixing means force-reinstalling the exact pinned version. Related: `pythonuserbase-in-zshenv`, `no-per-component-venvs`, `ccenv-installed-vs-source-version`.
