---
name: ccloop-install-heals-own-hooks
description: Top-level install.sh runs `ccloop install` after pip-installing ccloop so it self-heals stale PostToolUse/Stop hook paths
metadata:
  type: reference
tags: [install, ccloop, hooks, heal]
---

ccloop owns its own pair of hooks: `PostToolUse → ccloop guard` and `Stop → ccloop keepgoing`. These are auto-registered on first run of a ccloop task — they store the absolute path to the ccloop binary at registration time. When the ccloop binary moves (e.g. user upgrades from a manual venv at `~/.venvs/ccloop/bin/ccloop` to `~/.local/bin/ccloop`), the registrations stay pinned to the OLD path and every Claude Code Stop event fires a `ModuleNotFoundError: No module named 'ccloop'` from the dead path.

The top-level install.sh now runs `ccloop install` immediately after `pip3 install --user ccloop`. That subcommand calls `install.ensure_registered()`, which uses a deliberately loose `_is_ours` matcher (matches any command whose basename is `ccloop` or contains `ccloop`) to detect and rewrite stale entries — including legacy venv paths, the bash version of the hook, or whatever else accumulated. The user does NOT have to run `ccloop install` by hand.

Implementation lives in `ccloop/src/ccloop/install.py:_is_ours()` and `ensure_registered()`.

**Why:** This is the only ccenv component whose hooks aren't owned by `register_mcp()` or the top-level CLAUDE.md assembly — ccloop manages its own settings.json entries. Without the post-install `ccloop install` call, a user who'd ever done a manual venv ccloop install would see broken hook errors after every Stop event, even after a clean re-run of the top-level installer.

**How to apply:** If you ever extract or refactor ccloop's hook installer, preserve `_is_ours`'s loose matching behavior. A strict equality match (only heal if the new path = old path) would defeat the whole point. And keep the top-level install.sh's `ccloop install` invocation — it's deliberately not a one-time fix.
