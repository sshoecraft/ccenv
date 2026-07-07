---
name: no-per-component-venvs
description: ccenv installs via pip3 install --user; pipx is the PEP668 fallback for MANUAL per-component installs, but install.sh itself uses --break-system-pack…
metadata:
  type: feedback
tags: [install, venv, pip, pep668]
---

When normalizing install instructions across ccenv subdirs, do NOT recommend a venv per component (the old ccloop README pattern of `python3 -m venv ~/.venvs/ccloop && pip install .` plus a PATH symlink). User pushed back hard: "we'd have a venv for EACH component of this repo?" — the answer is no.

Standard: `pip3 install --user .` as the primary recommendation, with `pipx install .` as the PEP 668 fallback **for a manual, single-component install**. The top-level install.sh installs all five components with `pip install --user`.

**Why:** Five venvs means 5× the disk for shared deps (mcp, anyio, pydantic, etc.), five separate symlink chains to maintain, and inconsistent layouts across components — none of which buys isolation we actually need, because every component is the same user running against the same `claude` CLI.

**install.sh does NOT fall back to pipx (important nuance, v0.4.1):** the bundle installer puts all five components into ONE shared `--user` site so the `ccenvmcp` shim is importable across ccmemory/ccusage/ccteam — pipx's per-app venvs would break that cross-import. So when a distro blocks `--user` with PEP 668 (`error: externally-managed-environment`; the installer's `set -e` aborts on it), install.sh probes for the `EXTERNALLY-MANAGED` marker (`sysconfig.get_path("stdlib")`) and, only when present AND pip supports it, exports `PIP_BREAK_SYSTEM_PACKAGES=1` for its subshell — covering every `pip install --user` at once. Safe because everything is `--user` into `~/.local`, never system site-packages. Immediate manual unblock on any box: `PIP_BREAK_SYSTEM_PACKAGES=1 ./install.sh`. See [[shared-userbase-compiled-dep-abi-mismatch]], [[pythonuserbase-in-zshenv]].

**How to apply:** For a manual per-component install, default to `pip3 install --user .`, fall back to `pipx`. For install.sh / the bundle, PEP 668 is handled with the auto-detected `--break-system-packages` export above — do NOT convert install.sh to pipx. Never write per-component manual-venv recipes.
