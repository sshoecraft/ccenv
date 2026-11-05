---
name: compiled-ccenv-installed-version-and-pythonuserbase-env
description: ccenv environment: installed-version marker vs NFS-shared source VERSION; PYTHONUSERBASE must be in ~/.zshenv for Claude-spawned hooks/MCP.
metadata:
  type: project
tags: [compiled, install, version, nfs, pythonuserbase, zshenv]
---

Two ccenv environment traps: how to know what is installed, and how Python finds `--user` packages when Claude spawns processes. Complements `compiled-ccenv-environment-and-ccmemory-traps`.

## Installed version vs source version [[ccenv-installed-vs-source-version]]
Two version files answer different questions:
- `/src/ccenv/VERSION`: the SOURCE tree version; changes when someone or a `git pull` updates the tree.
- `~/.config/ccenv/installed-version`: the INSTALLED version, written by `install.sh` at the END of a successful run (after `set -e` would have aborted on failure), so always truthful.

`/src` is NFS-shared across clyde / serv / solardirector / infra / mac-mini / trader@*, so source VERSION is byte-identical everywhere instantly. `cat /src/ccenv/VERSION` as "is this machine current?" falsely says current once any machine updates the share. First hit: bumping 0.1.0 -> 0.1.1; clyde pushed it, six other systems read 0.1.1 from disk, `instenv.prompt` reported all "current at v0.1.1", none had re-run `install.sh`; pip packages, hooks and `~/.local/bin/...` were still 0.1.0 everywhere except clyde.
- Cross-machine "is ccenv current?" checks ALWAYS use `~/.config/ccenv/installed-version` (`instenv.prompt` does).
- Missing/empty marker (machine never ran install.sh since marker support) = needs install.
- Do NOT use `pip show <component>` for bundle version (ccloop 0.3.x, ccmemory 0.6.x are independent).
- Do NOT use commit SHAs (`git rev-parse HEAD` vs `git ls-remote`): they change on typo/README commits that do not bump VERSION.
- Related: `ccloop-stop-hook-return-0-kills-session`, same family: design must account for runtime context, not only local correctness.

## PYTHONUSERBASE belongs in ~/.zshenv [[pythonuserbase-in-zshenv]]
install.sh installs `--user` packages under `~/.local` (forces `PYTHONUSERBASE=$HOME/.local`). At runtime Python finds them only if `PYTHONUSERBASE` is in the environment; otherwise user-site falls back to the macOS default `~/Library/Python/<ver>/...` (empty) and every ccenv binary (ccmemory, ccloop, ccusage-statusline) dies with `ModuleNotFoundError`.
- Trap: `~/.zshrc` is sourced only by interactive shells; Claude Code runs hooks, the statusLine command and MCP servers in non-interactive shells, so an export there works in the terminal but silently fails for everything Claude spawns (blank statusline, broken ccloop/ccmemory hooks).
- Fix: `export PYTHONUSERBASE="$HOME/.local"` in `~/.zshenv` (sourced on every zsh invocation). Verified: `env -i HOME=$HOME /bin/zsh -c 'echo $PYTHONUSERBASE'` resolves it.
- Do NOT put it as `env` in `~/.claude/settings.json`: redundant (zshenv covers every Claude subprocess), non-portable (hardcoded absolute path), and install.sh does not write it so it would be missing on other machines.
- install.sh's "REQUIRED: shell environment setup" warning checks the live env var (not the rc file) and points at `~/.zshenv`.
- Related: `src-tree-appledouble-sidecars`.
