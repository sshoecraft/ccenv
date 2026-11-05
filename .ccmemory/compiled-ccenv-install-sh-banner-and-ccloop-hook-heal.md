---
name: compiled-ccenv-install-sh-banner-and-ccloop-hook-heal
description: Assorted install.sh behavior: PYTHONUSERBASE banner gated on platform default; top-level install runs `ccloop install` to heal stale hook paths.
metadata:
  type: project
tags: [compiled, install, ccloop, pythonuserbase, hooks]
---

Assorted pair (no shared topic beyond ccenv `install.sh` behavior).

## PYTHONUSERBASE banner gating [[pythonuserbase-banner-platform-gated]]
install.sh forces `PYTHONUSERBASE=$HOME/.local` for every `pip3 install --user`, so binaries land in `~/.local/bin/` everywhere. At runtime Python consults `PYTHONUSERBASE` only if it is in the environment; the install-time export does not travel. Unset, `site.py` falls back to the platform default user-base, finds nothing, and every ccenv binary fails with `ModuleNotFoundError` when Claude Code launches it as a hook or MCP subprocess.
The "REQUIRED: shell environment setup" banner is gated on the platform-default detector:
```
PLATFORM_USER_BASE=$(env -u PYTHONUSERBASE python3 -c 'import site; print(site.USER_BASE)')
PLATFORM_DEFAULTS_TO_LOCAL=0
[ "$PLATFORM_USER_BASE" = "$HOME/.local" ] && PLATFORM_DEFAULTS_TO_LOCAL=1
```
Linux default IS `~/.local` (posix_user scheme), so the override is a no-op and the banner stays silent. macOS with Homebrew Python defaults to `~/Library/Python/<ver>`: banner fires, names the actual default, tells the user to add the export to `~/.zshenv` (sourced by non-interactive zsh, which Claude Code uses to spawn hooks/MCPs; see `pythonuserbase-in-zshenv`).
Why: earlier versions fired whenever `PYTHONUSERBASE` was unset, i.e. every Linux install, a false "REQUIRED" prompt. How to apply: on refactor keep both gates, platform default AND the user's `ORIGINAL_PYTHONUSERBASE`.

## ccloop heals its own hooks at install [[ccloop-install-heals-own-hooks]]
ccloop owns `PostToolUse -> ccloop guard` and `Stop -> ccloop keepgoing`, auto-registered on first ccloop task run with the absolute binary path at registration time. If the binary moves (e.g. manual venv `~/.venvs/ccloop/bin/ccloop` -> `~/.local/bin/ccloop`), registrations stay pinned to the old path and every Stop event fires `ModuleNotFoundError: No module named 'ccloop'`.
Top-level install.sh runs `ccloop install` right after `pip3 install --user ccloop`; it calls `install.ensure_registered()`, whose deliberately loose `_is_ours` matcher (any command whose basename is or contains `ccloop`) rewrites stale entries including legacy venv paths and the old bash hook. Code: `ccloop/src/ccloop/install.py:_is_ours()` and `ensure_registered()`. The user need not run it by hand.
Why: ccloop is the only component whose hooks are not owned by `register_mcp()` or the top-level CLAUDE.md assembly; without the call, anyone who ever did a manual venv install sees broken hook errors after every Stop even after a clean top-level re-run. How to apply: preserve `_is_ours`'s loose matching (strict equality defeats the purpose) and keep the `ccloop install` call in install.sh; it is not a one-time fix.
