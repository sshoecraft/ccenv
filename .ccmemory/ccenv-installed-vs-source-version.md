---
name: ccenv-installed-vs-source-version
description: "Installed ccenv version" lives in ~/.config/ccenv/installed-version, NOT /src/ccenv/VERSION. NFS-shared /src makes the source VERSION useless as an…
metadata:
  type: project
tags: [install, version, nfs]
---

There are TWO version files for ccenv and they answer DIFFERENT questions. Confusing them produced a real false-positive bug during cross-machine version checks.

**`/src/ccenv/VERSION`** — the *source code* version. Reflects what bundle version is sitting in the source tree. Changes when someone (or someone's `git pull`) updates the tree.

**`~/.config/ccenv/installed-version`** — the *installed* version. Written by `install.sh` on successful completion to record what was actually installed on THIS machine.

When `/src` is **NFS-shared across multiple machines** (Steve's setup: clyde / serv / solardirector / infra / mac-mini / trader@*), the source VERSION is byte-identical on every system instantly when any one of them updates the share. So checking `cat /src/ccenv/VERSION` to determine "is this machine's install current?" is wrong — it falsely reports "current" the moment one machine updates the share, even if the OTHER machines haven't run `install.sh` yet.

The first cross-machine update with the VERSION file (bumping 0.1.0 → 0.1.1) hit this exactly: clyde pushed `0.1.1`, all six other systems on the shared NFS instantly read `0.1.1` from disk, the `instenv.prompt` reported every system as "current at v0.1.1," and none of them had actually re-run `install.sh`. The installed bits (pip packages, hooks, ~/.local/bin/...) were all still at 0.1.0 on every machine except clyde.

**Why:** `git pull` (or an NFS write) updates the source tree, not the installed bits. Only `./install.sh` updates the installed bits. So "installed version" must be recorded by `install.sh` itself, not derived from the source tree.

**How to apply:**

- For cross-machine "is ccenv current?" checks, ALWAYS use `~/.config/ccenv/installed-version`. `instenv.prompt` does this; any future tooling should too.
- `install.sh` writes the marker at the END of a successful run (after `set -e` would have aborted on any earlier failure), so the marker is always truthful.
- The marker is missing on a fresh machine that's never run `install.sh` since marker support was added — treat missing/empty as "needs install."
- Do NOT use `pip show <component>` to determine ccenv bundle version. Component versions (ccloop 0.3.x, ccmemory 0.6.x) are independent and don't track the bundle.
- Do NOT use commit SHAs (`git rev-parse HEAD` vs `git ls-remote`) for this — SHAs change on every commit (typo fixes, READMEs) and would flag "needs update" on changes that don't actually bump VERSION.

Related: [[ccloop-stop-hook-return-0-kills-session]] — same lesson family of "design decisions need to account for the runtime context, not just the local correctness of a function."
