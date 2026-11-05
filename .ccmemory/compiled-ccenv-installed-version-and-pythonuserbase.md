---
name: compiled-ccenv-installed-version-and-pythonuserbase
description: ccenv installed-version marker vs NFS-shared source VERSION, and PYTHONUSERBASE needing ~/.zshenv not ~/.zshrc. Companion to the environment-traps ar…
metadata:
  type: project
tags: [compiled, ccenv, install, version, nfs, zshenv]
---

Two install-environment traps. Backfill part split from [[compiled-ccenv-environment-and-ccmemory-traps]] (kept under the size ceiling).

## Installed version vs source version
Source: [[ccenv-installed-vs-source-version]]
- Two version files answer different questions. `/src/ccenv/VERSION` = source-tree version (changes on someone's edit or `git pull`). `~/.config/ccenv/installed-version` = what `install.sh` actually installed on THIS machine, written at the END of a successful run (after `set -e` would have aborted on earlier failure), so always truthful.
- /src is NFS-shared across machines (clyde / serv / solardirector / infra / mac-mini / trader@*): source VERSION is byte-identical everywhere the instant one machine updates it. Using `cat /src/ccenv/VERSION` for "is this install current?" falsely says current. First cross-machine update (0.1.0 -> 0.1.1) hit this: clyde pushed 0.1.1, six others read 0.1.1, `instenv.prompt` reported all "current at v0.1.1", none had run `install.sh`; installed bits (pip packages, hooks, `~/.local/bin/...`) stayed 0.1.0 everywhere except clyde.
- Why: git pull / NFS write updates the tree, only `./install.sh` updates installed bits.
- Apply: cross-machine checks ALWAYS use `~/.config/ccenv/installed-version` (`instenv.prompt` does). Missing/empty marker on a machine that never ran a marker-aware install = needs install. Do NOT use `pip show <component>` (component versions, ccloop 0.3.x / ccmemory 0.6.x, are independent of the bundle). Do NOT use commit SHAs (`git rev-parse HEAD` vs `git ls-remote`): they change on typo/README commits that do not bump VERSION.
- Related: `ccloop-stop-hook-return-0-kills-session`, same family: design must account for runtime context, not just local correctness.

## PYTHONUSERBASE belongs in ~/.zshenv
Source: [[pythonuserbase-in-zshenv]]
- ccenv installs Python `--user` packages under `~/.local` (install.sh forces `PYTHONUSERBASE=$HOME/.local`). At runtime Python finds them only if `PYTHONUSERBASE` is in the environment; otherwise user-site falls back to the macOS default `~/Library/Python/<ver>/...` (empty) and every ccenv binary (ccmemory, ccloop, ccusage-statusline) dies with `ModuleNotFoundError`.
- Trap: `~/.zshrc` is sourced only by interactive shells. Claude Code runs hooks, the statusLine command and MCP servers in non-interactive shells, which skip it: works in the terminal, silently fails for everything Claude spawns (blank statusline, broken ccloop/ccmemory hooks).
- Fix: `export PYTHONUSERBASE="$HOME/.local"` in `~/.zshenv` (sourced on every zsh invocation). Verified: `env -i HOME=$HOME /bin/zsh -c 'echo $PYTHONUSERBASE'`.
- Do NOT add it as `env` in `~/.claude/settings.json`: redundant, non-portable (hardcoded absolute path), and install.sh does not write it so it would not exist on other machines. install.sh's "REQUIRED: shell environment setup" warning checks the live env var (not the rc file) and now points at `~/.zshenv`.
- Related: `src-tree-appledouble-sidecars`.
