---
name: ccmemory-debian-build-unknown-install-layout
description: RESOLVED + VERIFIED on-box (2026-06-13): install.sh ensure_build_toolchain bootstrap fixed the PEP 621 UNKNOWN/install_layout build crash on Debian 3…
metadata:
  type: project
---

RESOLVED and VERIFIED on the Debian box `serv` (Python 3.9.2, pip 20.3.4, setuptools 52) on 2026-06-13.

Original failure: `./install.sh` built every PEP 621 package as `UNKNOWN` then tripped `AttributeError: install_layout` — because the build ran under Debian's SYSTEM setuptools 52 (too old for PEP 621), with build isolation effectively bypassed. Independent of the mcp/3.10 issue; affected ALL packages.

FIX (in install.sh): `ensure_build_toolchain()` upgrades pip/setuptools/wheel into ~/.local (PYTHONUSERBASE) when setuptools major < 61; `pip_install_local()` uses `python3 -m pip` so the upgraded user-site toolchain is actually used.

VERIFIED on `serv`: bootstrap upgraded setuptools 52→82, pip 20.3.4→26.0.1, wheel→0.47 into ~/.local. Then ALL packages built clean wheels with NO UNKNOWN/install_layout: ccenvmcp 0.1.0, ccmemory 0.8.0, ccusage 0.2.0, ccloop 0.3.4, ccteam 0.3.0. All three MCP servers `claude mcp list` → ✔ Connected on 3.9.2. Confirmed only `mcp` was the 3.9 blocker: nats-py 2.15.0 and watchfiles 1.1.1 both fetched cp39 wheels and installed fine.

Benign warnings seen on that box (NOT caused by ccenv, safe to ignore): "Error parsing dependencies of gpg: Invalid version '1.14.0-unknown'" (broken system python3-gpg metadata), and a pipx 1.0.0 argcomplete/userpath conflict note from pip's resolver (pre-existing system pipx; ccenv uses pip --user, not pipx). Related: [[ccenvmcp-stdlib-mcp-shim]], [[pythonuserbase-in-zshenv]], [[no-per-component-venvs]].
