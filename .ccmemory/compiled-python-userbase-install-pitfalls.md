---
name: compiled-python-userbase-install-pitfalls
description: ccenv Python --user install traps: shared PYTHONUSERBASE ABI strandings, zshenv export, banner gating, stdlib MCP shim, old setuptools build crash
metadata:
  type: project
tags: [compiled, install, pythonuserbase, python3.9, abi, mcp]
---

Central topic: how ccenv's `pip --user` install under a forced `PYTHONUSERBASE=$HOME/.local` behaves across platforms and Python versions, and the failures that came out of it.

## Environment: PYTHONUSERBASE must reach every Claude subprocess
- install.sh forces `PYTHONUSERBASE=$HOME/.local` at install time, but at runtime Python only honours it if it is in the environment. Otherwise user-site falls back to the platform default (macOS Homebrew: `~/Library/Python/<ver>`), which is empty, and every ccenv binary dies with `ModuleNotFoundError` ([[pythonuserbase-in-zshenv]]).
- Export it in `~/.zshenv`, not `~/.zshrc`. Hooks, statusLine and MCP servers run in non-interactive shells that skip `.zshrc`. Do not put it in `~/.claude/settings.json` `env`: redundant, hardcoded path, not written by install.sh ([[pythonuserbase-in-zshenv]]).
- The "REQUIRED: shell environment setup" banner is gated on the platform default (`env -u PYTHONUSERBASE python3 -c 'import site; print(site.USER_BASE)'` == `$HOME/.local`). Linux default is `~/.local` (posix_user), so the banner stays silent there; it fires on macOS+Homebrew and names `~/.zshenv`. Earlier versions fired whenever the var was unset, a false positive on every Linux install. Keep both gates (platform default AND the user's original value) in any refactor ([[pythonuserbase-banner-platform-gated]]).

## Shared version-agnostic site-packages strands compiled deps
- Homebrew's `osx_framework_user` scheme makes the user site a single `lib/python/site-packages` shared by all interpreters. Pure-Python deps survive an interpreter bump; compiled extensions are ABI-tagged and load only under the matching Python ([[shared-userbase-compiled-dep-abi-mismatch]]).
- Seen 2026-06-13: ccteam MCP failed with `No module named 'watchfiles._rust_notify'` after a 3.9 to 3.14 bump; scan found 12 stale cp39 `.so` across 7 dists. Pip skipped refetching because the dists looked installed. ccmemory/ccusage were unaffected because they are stdlib-only ([[ccenvmcp-stdlib-mcp-shim]]).
- Manual fix: `python3.14 -m pip install --user --force-reinstall --no-deps <dist>`.
- Automated fix in install.sh v0.1.5, `heal_stale_compiled_exts()`: find `.so/.pyd/.dylib` whose ABI tag differs from the running `EXT_SUFFIX` (skip `.abi3` and untagged), map to the owning dist via `RECORD`, force-reinstall the exact `name==version` with `--no-deps`, never `--upgrade`. Keys off on-disk files so it heals already-broken boxes; writes `~/.config/ccenv/python-tag` to announce bumps ([[shared-userbase-compiled-dep-abi-mismatch]]).

## Python 3.9 support
- The official `mcp` SDK requires Python >=3.10 and was the only 3.9 blocker (Debian 11, RHEL 9 family, Ubuntu 20.04). Fix: `ccenvmcp/`, a stdlib-only FastMCP-compatible shim used by ccmemory, ccusage and ccteam; `mcp` dropped from deps ([[ccenvmcp-stdlib-mcp-shim]]).
- 3.9 pitfall: never use `typing.get_type_hints()` for schema building. Under `from __future__ import annotations` it evaluates strings like `"int | None"` and raises TypeError on 3.9; the shim parses annotation text instead.
- ccenvmcp is installed first and intentionally not a declared dependency (local-source installs; a declared dep triggers a failing PyPI lookup) ([[ccenvmcp-stdlib-mcp-shim]]).
- On Debian (serv, Python 3.9.2) every PEP 621 package built as `UNKNOWN` then crashed with `AttributeError: install_layout`, due to system setuptools 52 being too old. Fix: `ensure_build_toolchain()` upgrades pip/setuptools/wheel into `~/.local` when setuptools major < 61, and `pip_install_local()` uses `python3 -m pip` so the user-site toolchain is used. Verified: all packages built clean and all three MCP servers connected. Warnings about gpg metadata and pipx/argcomplete are pre-existing and benign ([[ccmemory-debian-build-unknown-install-layout]]).

Related: [[no-per-component-venvs]], [[ccenv-installed-vs-source-version]], [[src-tree-appledouble-sidecars]].
