---
name: compiled-ccenv-install-sh-build-env-and-hook-healing
description: Assorted install.sh notes: Debian PEP 621 build-toolchain fix, platform-gated PYTHONUSERBASE banner, ccloop self-healing hook registration.
metadata:
  type: project
tags: [compiled, ccenv, install.sh, pythonuserbase, ccloop, debian]
---

# ccenv install.sh: build toolchain, PYTHONUSERBASE banner, hook healing

Assorted group; notes share only that they describe install.sh behavior.

## Debian build crash (UNKNOWN / install_layout) (`ccmemory-debian-build-unknown-install-layout`)
- RESOLVED and VERIFIED 2026-06-13 on Debian box `serv` (Python 3.9.2, pip 20.3.4, setuptools 52). Original failure: `./install.sh` built every PEP 621 package as `UNKNOWN` then hit `AttributeError: install_layout`, because the build ran under Debian's SYSTEM setuptools 52 (too old for PEP 621), build isolation effectively bypassed. Independent of the mcp/3.10 issue; affected ALL packages.
- Fix in install.sh: `ensure_build_toolchain()` upgrades pip/setuptools/wheel into `~/.local` (PYTHONUSERBASE) when setuptools major < 61; `pip_install_local()` uses `python3 -m pip` so the upgraded user-site toolchain is used.
- Verified: setuptools 52 -> 82, pip 20.3.4 -> 26.0.1, wheel -> 0.47; all packages built clean (ccenvmcp 0.1.0, ccmemory 0.8.0, ccusage 0.2.0, ccloop 0.3.4, ccteam 0.3.0); three MCP servers `claude mcp list` Connected on 3.9.2. Only `mcp` was the 3.9 blocker: nats-py 2.15.0 and watchfiles 1.1.1 fetched cp39 wheels fine.
- Benign, not ccenv's, ignore: "Error parsing dependencies of gpg: Invalid version '1.14.0-unknown'" (broken system python3-gpg metadata); pipx 1.0.0 argcomplete/userpath conflict note from pip's resolver (system pipx; ccenv uses pip --user). Related `ccenvmcp-stdlib-mcp-shim`, `pythonuserbase-in-zshenv`, `no-per-component-venvs`.

## REQUIRED-setup banner is platform-gated (`pythonuserbase-banner-platform-gated`)
- install.sh forces `PYTHONUSERBASE=$HOME/.local` for every `pip3 install --user`, so binaries land in `~/.local/bin/`. At runtime Python consults `PYTHONUSERBASE` only if it is in the environment; otherwise `site.py` falls back to the platform default, finds nothing, and every ccenv binary fails with `ModuleNotFoundError` when Claude Code launches it as a hook or MCP subprocess.
- Banner gate:
```
PLATFORM_USER_BASE=$(env -u PYTHONUSERBASE python3 -c 'import site; print(site.USER_BASE)')
PLATFORM_DEFAULTS_TO_LOCAL=0
[ "$PLATFORM_USER_BASE" = "$HOME/.local" ] && PLATFORM_DEFAULTS_TO_LOCAL=1
```
Linux default IS `~/.local` (posix_user scheme): override is a no-op, banner silent. macOS + Homebrew Python defaults to `~/Library/Python/<ver>`: banner fires, names the real default, tells the user to export in `~/.zshenv` (sourced by non-interactive zsh, which Claude Code uses to spawn hooks/MCPs; see `pythonuserbase-in-zshenv`).
- Why: earlier versions fired whenever `PYTHONUSERBASE` was unset (every Linux install), a false "REQUIRED" prompt. Keep both gates when refactoring: platform default AND the user's `ORIGINAL_PYTHONUSERBASE`.

## ccloop install heals its own hooks (`ccloop-install-heals-own-hooks`)
- ccloop owns `PostToolUse -> ccloop guard` and `Stop -> ccloop keepgoing`, auto-registered on first ccloop task with the absolute binary path. If the binary moves (e.g. manual venv `~/.venvs/ccloop/bin/ccloop` -> `~/.local/bin/ccloop`) the registrations stay pinned to the old path and every Stop event fires `ModuleNotFoundError: No module named 'ccloop'`.
- Top-level install.sh runs `ccloop install` right after `pip3 install --user ccloop`; it calls `install.ensure_registered()` using a deliberately loose `_is_ours` matcher (basename is or contains `ccloop`) to rewrite stale entries incl. legacy venv paths and the old bash hook. Code: `ccloop/src/ccloop/install.py:_is_ours()` and `ensure_registered()`.
- Why: ccloop is the only component whose hooks aren't owned by `register_mcp()` or the top-level CLAUDE.md assembly. Apply: preserve `_is_ours`'s loose matching (strict equality defeats the point) and keep install.sh's `ccloop install` call; it is not a one-time fix.
