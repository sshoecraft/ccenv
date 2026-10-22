---
name: compiled-python-userbase-install-pitfalls
description: ccenv Python --user install traps: shared PYTHONUSERBASE ABI strandings, zshenv export, banner gating, stdlib MCP shim, old setuptools build crash
metadata:
  type: project
tags: [compiled, install, pythonuserbase, python3.9, abi, mcp]
---

Central topic: how ccenv's `pip --user` install under a forced `PYTHONUSERBASE=$HOME/.local` behaves across platforms and Python versions, and the failures that came out of it.

## Environment: PYTHONUSERBASE must reach every Claude subprocess
- install.sh forces `PYTHONUSERBASE=$HOME/.local` for every `pip3 install --user`, so binaries land in `~/.local/bin/` on every platform. At runtime Python only honours it if it is in the environment; the install-time export does not travel with the scripts. Otherwise `site.py` falls back to the platform default user-base (macOS Homebrew: `~/Library/Python/<ver>`), which is empty, and every ccenv binary dies with `ModuleNotFoundError` when Claude Code launches it as a hook or MCP subprocess ([[pythonuserbase-in-zshenv]], `pythonuserbase-banner-platform-gated`).
- Export it in `~/.zshenv`, not `~/.zshrc`. Hooks, statusLine and MCP servers run in non-interactive shells that skip `.zshrc` (`.zshenv` is the file zsh sources for non-interactive shells). Do not put it in `~/.claude/settings.json` `env`: redundant, hardcoded path, not written by install.sh ([[pythonuserbase-in-zshenv]]).
- The "REQUIRED: shell environment setup" banner at the end of install.sh is gated on the platform default:
  ```
  PLATFORM_USER_BASE=$(env -u PYTHONUSERBASE python3 -c 'import site; print(site.USER_BASE)')
  PLATFORM_DEFAULTS_TO_LOCAL=0
  [ "$PLATFORM_USER_BASE" = "$HOME/.local" ] && PLATFORM_DEFAULTS_TO_LOCAL=1
  ```
  Linux default is `~/.local` (posix_user scheme), so the override is a no-op and the banner stays silent. On macOS+Homebrew it fires, names the actual platform default in the body, and tells the user to add the export to `~/.zshenv`. Earlier versions fired whenever the var was unset, a false positive on every Linux install. Refactor rule: keep both gates (platform default AND the user's `ORIGINAL_PYTHONUSERBASE`); dropping the platform-default check is the difference between a quiet Linux install and a nagging one (`pythonuserbase-banner-platform-gated`).

## Shared version-agnostic site-packages strands compiled deps
- `PYTHONUSERBASE=~/.local` + Homebrew's `osx_framework_user` scheme collapses the user site to a single version-agnostic `lib/python/site-packages` shared by all interpreters (3.9, 3.13, 3.14...). Verify: `python3 -c 'import site;print(site.getusersitepackages())'` returns the SAME path under 3.13 and 3.14. Pure-Python deps survive an interpreter bump; compiled extensions are ABI-tagged (`*.cpython-39-darwin.so` vs `*.cpython-314-darwin.so`) and load only under the matching Python (`shared-userbase-compiled-dep-abi-mismatch`).
- Seen 2026-06-13: ccteam MCP `Failed to connect`. Launcher runs Python 3.14 but the shared dir held only `watchfiles/_rust_notify.cpython-39-darwin.so` (a 3.9 build) -> `ModuleNotFoundError: No module named 'watchfiles._rust_notify'`, after a 3.9 to 3.14 bump. Scan found 12 stale cp39 `.so` across 7 dists (watchfiles, cffi, websockets, SQLAlchemy, uvloop, httptools, PyYAML, pydantic_core): the whole shared site was stranded, not just ccteam. Pip never refetched right-ABI wheels because the dists looked installed. ccmemory/ccusage were unaffected because they are stdlib-only (ccenvmcp shim); ccteam is the only component with a compiled dep (`shared-userbase-compiled-dep-abi-mismatch`, `ccenvmcp-stdlib-mcp-shim`).
- Manual fix (one dist): `python3.14 -m pip install --user --force-reinstall --no-deps watchfiles` (or `<dist>`).
- Automated fix in install.sh v0.1.5, `heal_stale_compiled_exts()`, runs after all components install: walk the shared user-site for `.so/.pyd/.dylib` whose ABI tag differs from the running `EXT_SUFFIX` (skip `.abi3.so` and untagged), map each to its owning dist via `RECORD`, force-reinstall the exact `name==version` with `--force-reinstall --no-deps`, never `--upgrade` (rebuilds the same release for the new ABI without surprise-upgrading packages ccenv doesn't own). Generic; keys off on-disk files, not a marker, so it heals already-broken boxes. Writes `~/.config/ccenv/python-tag` (`sys.implementation.cache_tag`) so the next run can announce a bump. Docs: `docs/install.md`. Re-running `./install.sh` auto-heals; if hand-fixing, force-reinstall the exact pinned version (`shared-userbase-compiled-dep-abi-mismatch`).

## Python 3.9 support: ccenvmcp shim
- Every release of the official `mcp` SDK requires Python >=3.10; it was the ONLY hard 3.9 blocker (Debian 11 / Raspberry Pi OS, RHEL/Alma/Rocky 9, Ubuntu 20.04). `nats-py` needs only >=3.7, `watchfiles>=0.21` resolves to a 3.9-compatible abi3 build, all packages already use `from __future__ import annotations`.
- Fix (2026-06-13): foundation package `ccenvmcp/`, ~250-line stdlib-only FastMCP-compatible shim. API: `from ccenvmcp import FastMCP`; `FastMCP(name, instructions=...)`, `@app.tool()` on sync or async funcs, `@app.tool(name=, description=, schema=)` overrides, `app.run()` (sync), `await app.run_stdio_async()`. Wire protocol: newline-delimited JSON-RPC 2.0 over stdio: initialize / notifications/initialized / tools/list / tools/call / ping (`ccenvmcp-stdlib-mcp-shim`).
- 3.9 pitfall (in `ccenvmcp/ccenvmcp/transport.py`): never use `typing.get_type_hints()` for schema building. Under `from __future__ import annotations` it evaluates strings like `"int | None"` and raises TypeError on 3.9. The shim parses annotation text instead (`_token_to_schema`; handles `X | None`, `Optional[X]`, `list[str]`). Tests: `ccenvmcp/tests/test_transport.py`.
- Consumers: ccmemory (low-level style, 5 tools, explicit `schema=` to preserve the memory_write `type` enum; `serve()` split into `build_app()`+`serve()`), ccusage (2 sync tools), ccteam (9 async tools, `instructions=`). `mcp` removed from deps, requires-python lowered to >=3.9. Bumped: ccmemory 0.7.0, ccusage 0.2.0, ccteam 0.3.0.
- Distribution: ccenvmcp is installed FIRST by top-level install.sh and intentionally NOT a declared dependency (local-source installs; a declared dep triggers a failing PyPI lookup). Install ordering makes it importable from the shared --user site. See `ccenvmcp/docs/mcp.md` (`ccenvmcp-stdlib-mcp-shim`).

## Debian old-setuptools build crash (RESOLVED, verified on `serv`)
- Box `serv`: Debian, Python 3.9.2, pip 20.3.4, system setuptools 52. `./install.sh` built every PEP 621 package as `UNKNOWN` then crashed `AttributeError: install_layout`: build ran under system setuptools 52 (too old for PEP 621), build isolation effectively bypassed. Independent of the mcp/3.10 issue; affected ALL packages.
- Fix in install.sh: `ensure_build_toolchain()` upgrades pip/setuptools/wheel into `~/.local` when setuptools major < 61; `pip_install_local()` uses `python3 -m pip` so the user-site toolchain is used.
- Verified 2026-06-13: setuptools 52->82, pip 20.3.4->26.0.1, wheel->0.47; all packages built clean with no UNKNOWN/install_layout: ccenvmcp 0.1.0, ccmemory 0.8.0, ccusage 0.2.0, ccloop 0.3.4, ccteam 0.3.0. All three MCP servers `claude mcp list` -> Connected on 3.9.2. nats-py 2.15.0 and watchfiles 1.1.1 fetched cp39 wheels fine, confirming `mcp` was the only 3.9 blocker.
- Benign pre-existing warnings, ignore: "Error parsing dependencies of gpg: Invalid version '1.14.0-unknown'" (broken system python3-gpg metadata); pipx 1.0.0 argcomplete/userpath resolver note (ccenv uses pip --user, not pipx) (`ccmemory-debian-build-unknown-install-layout`).

Related: `no-per-component-venvs`, `ccenv-installed-vs-source-version`, [[src-tree-appledouble-sidecars]].
