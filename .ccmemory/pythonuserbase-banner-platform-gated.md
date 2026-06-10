---
name: pythonuserbase-banner-platform-gated
description: install.sh's REQUIRED-setup banner is gated on the platform's DEFAULT user-base; silent on Linux, fires on macOS+Homebrew Python
metadata:
  type: project
tags: [install, pythonuserbase, platform]
---

install.sh forces `PYTHONUSERBASE=$HOME/.local` for every `pip3 install --user`, so on every platform the binaries land at `~/.local/bin/`. But at *runtime* Python only consults `PYTHONUSERBASE` if it's in the environment — the install-time export doesn't travel with the scripts. Without it set in the user's shell env, Python's `site.py` falls back to the platform default user-base, looks for packages there, finds nothing, and every ccenv binary fails with `ModuleNotFoundError` when Claude Code launches it as a hook or MCP subprocess.

The "REQUIRED: shell environment setup" banner at the end of install.sh is gated on the platform-default detector:

    PLATFORM_USER_BASE=$(env -u PYTHONUSERBASE python3 -c 'import site; print(site.USER_BASE)')
    PLATFORM_DEFAULTS_TO_LOCAL=0
    [ "$PLATFORM_USER_BASE" = "$HOME/.local" ] && PLATFORM_DEFAULTS_TO_LOCAL=1

On Linux the platform default IS `~/.local` (posix_user scheme) — so the override is a no-op, and the banner correctly stays silent. On macOS with Homebrew Python the default is `~/Library/Python/<ver>` — the banner fires there, names the actual platform default in the body, and tells the user to add the export to `~/.zshenv` (the file zsh sources for non-interactive shells, which is what Claude Code uses to spawn hooks/MCPs — see [[pythonuserbase-in-zshenv]]).

**Why:** Earlier versions fired the banner whenever `PYTHONUSERBASE` was unset, which on Linux was every install — confusing the user with a "REQUIRED" prompt to set what was already the default. The platform-default detector eliminates the false positive.

**How to apply:** If you ever refactor the banner logic, keep both gates: detect the platform default AND check the user's `ORIGINAL_PYTHONUSERBASE`. Don't simplify away the platform-default check — it's the difference between a quiet Linux install and a nagging one.
