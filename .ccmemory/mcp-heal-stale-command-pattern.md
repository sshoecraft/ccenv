---
name: mcp-heal-stale-command-pattern
description: install.sh's register_mcp() compares stored Command+Args to the desired command and re-registers when stale — never simplify it away
metadata:
  type: reference
tags: [install, mcp, heal]
---

The `register_mcp()` function in install.sh does more than a check-and-skip. It:

1. Calls `mcp_registered "$name"` to check existence
2. If present, calls `mcp_current_command "$name"` which parses `claude mcp get` output, gluing `Command:` and `Args:` lines back together into a single string
3. Compares the parsed command to `$*` (what we'd register now)
4. If they differ: prints "registered with stale command — re-registering", calls `claude mcp remove`, then `claude mcp add` with the new command

This pattern is essential. Without it, an MCP server registered with a stale binary path (e.g. bare name `ccmemory` from a pre-PYTHONUSERBASE install, or `~/Library/Python/3.14/bin/ccmemory mcp` after the user switched to ~/.local) stays broken forever because `mcp_registered` returns true and the old skip-if-registered logic walked away.

Similar healing logic lives in `ccusage/install.py:register_mcp_user()` — same compare-and-heal, just in Python.

**Why:** Before this pattern existed, the failure mode was: install once with wrong path → MCP entry pinned to that wrong path → `claude mcp list` shows ✘ Failed to connect → re-running install.sh does nothing because the entry "exists" → user has to manually `claude mcp remove ccmemory` and re-run. This dragged on for an entire session before being identified as the root cause of ccmemory/ccteam connection failures on macOS.

**How to apply:** When extending the installer to register new MCPs, always go through `register_mcp()` — never inline a `claude mcp add` directly. If you add a new component with its own installer (like ccusage's install.py), replicate the heal pattern there too; don't bring back "leaving as-is" behavior.
