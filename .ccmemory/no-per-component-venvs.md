---
name: no-per-component-venvs
description: ccenv components install via pip3 install --user (pipx as PEP 668 fallback); per-component venvs are explicitly rejected
metadata:
  type: feedback
tags: [install, venv, pip]
---

When normalizing install instructions across ccenv subdirs, do NOT recommend a venv per component (the old ccloop README pattern of `python3 -m venv ~/.venvs/ccloop && pip install .` plus a PATH symlink). User pushed back hard: "we'd have a venv for EACH component of this repo?" — the answer is no.

Standard: `pip3 install --user .` as the primary recommendation, with `pipx install .` as the PEP 668 fallback. The top-level install.sh enforces this for all five components.

**Why:** Five venvs means 5× the disk for shared deps (mcp, anyio, pydantic, etc.), five separate symlink chains to maintain, and inconsistent layouts across components — none of which buys isolation we actually need, because every component is the same user running against the same `claude` CLI.

**How to apply:** Any time you're tempted to recommend a venv "for cleanliness" or because a distro blocks `--user` with PEP 668, default to `pip3 install --user .` first and only fall back to pipx (which manages its own venv invisibly). Never write per-component manual-venv recipes.
