---
name: compiled-install-sh-pythonuserbase-banner-and-ccloop-hook-heal
description: Assorted install.sh behaviors: PYTHONUSERBASE banner gated on platform default; top-level install runs ccloop install to heal stale hook paths
metadata:
  type: project
tags: [compiled, install, ccloop, pythonuserbase, hooks]
---

Assorted group (kind: assorted): two install.sh behaviors that share only the installer.

## PYTHONUSERBASE banner is platform-gated
- install.sh forces `PYTHONUSERBASE=$HOME/.local` for every `pip3 install --user`, so binaries land in `~/.local/bin/` everywhere. At runtime Python consults `PYTHONUSERBASE` only if in the environment; the install-time export does not travel. If unset in the user's shell env, `site.py` falls back to the platform default user-base, finds nothing, and every ccenv binary fails with `ModuleNotFoundError` when Claude Code launches it as hook or MCP subprocess. [[pythonuserbase-banner-platform-gated]]
- The "REQUIRED: shell environment setup" banner at the end of install.sh is gated on:
```
PLATFORM_USER_BASE=$(env -u PYTHONUSERBASE python3 -c 'import site; print(site.USER_BASE)')
PLATFORM_DEFAULTS_TO_LOCAL=0
[ "$PLATFORM_USER_BASE" = "$HOME/.local" ] && PLATFORM_DEFAULTS_TO_LOCAL=1
```
  Linux default IS `~/.local` (posix_user scheme), so the banner stays silent. macOS + Homebrew Python defaults to `~/Library/Python/<ver>`: banner fires, names the actual default, and says to add the export to `~/.zshenv` (sourced by zsh for non-interactive shells, which is how Claude Code spawns hooks/MCPs; see `pythonuserbase-in-zshenv`). [[pythonuserbase-banner-platform-gated]]
- Why: earlier versions fired whenever `PYTHONUSERBASE` was unset, i.e. every Linux install, nagging about what was already the default. If refactoring, keep both gates: platform-default detection AND the user's `ORIGINAL_PYTHONUSERBASE` check. [[pythonuserbase-banner-platform-gated]]

## ccloop install heals its own hooks
- ccloop owns two hooks: `PostToolUse -> ccloop guard` and `Stop -> ccloop keepgoing`, auto-registered on first ccloop task with the absolute binary path. When the binary moves (e.g. manual venv `~/.venvs/ccloop/bin/ccloop` -> `~/.local/bin/ccloop`), registrations stay pinned to the old path and every Stop event fires `ModuleNotFoundError: No module named 'ccloop'` from the dead path. [[ccloop-install-heals-own-hooks]]
- Top-level install.sh runs `ccloop install` right after `pip3 install --user ccloop`; it calls `install.ensure_registered()`, whose deliberately loose `_is_ours` matcher (basename is or contains `ccloop`) detects and rewrites stale entries, including legacy venv paths and the old bash hook. Code: `ccloop/src/ccloop/install.py:_is_ours()` and `ensure_registered()`. The user need not run `ccloop install` by hand. [[ccloop-install-heals-own-hooks]]
- Why: ccloop is the only component whose hooks are not owned by `register_mcp()` or the top-level CLAUDE.md assembly; without the post-install call, anyone who ever did a manual-venv install keeps broken hook errors even after a clean re-run. Preserve `_is_ours`'s loose matching (strict equality defeats the purpose) and keep the `ccloop install` call in install.sh (not a one-time fix). [[ccloop-install-heals-own-hooks]]
