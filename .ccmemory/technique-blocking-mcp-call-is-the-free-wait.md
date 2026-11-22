---
name: technique-blocking-mcp-call-is-the-free-wait
description: A headless claude session suspended in a blocking MCP tool call costs nothing; Bash waits cost a request per Stop-hook re-feed. Verified live 2026-11…
metadata:
  type: project
---

For any session that must wait (a resource queue, a long test run, the next work item), make the wait a blocking MCP tool call, not Bash.

**Why:** a Bash call ends at 10 minutes, so long work goes to the background and ccloop's Stop hook re-feeds the session (a full request with the whole context) until it ends. In mxfs, 3,632 of 19,054 worker requests (19%) were those re-feeds. An MCP call that does not return keeps the session suspended in place: no turns, no tokens.

**Limits:** Claude Code aborts an MCP call silent for 1800 s, so send `notifications/progress` every ~60 s (ccenvmcp 0.2.0: declare a `ctx` param, `await ctx.report_progress(...)`). Interactive sessions move a call over 2 minutes to the background (`CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS=0` turns that off); headless `-p` is exempt. The wall-clock cap (`MCP_TOOL_TIMEOUT`) is ~28 h.

**Verified:** `ccdirector/tests/live_mcp_worker.py` — `claude -p --mcp-config <file>` calling the worker server's `run` on a 150 s command returned in place with the result after 155 s (Claude Code 2.1.3xx, haiku).

**Trap paired with it:** a pool that kills "silent" workers must count a blocking tool as activity. ccdirector's server touches `.ccdirector/workers/<name>.beat` every minute while a tool blocks, and the pool's stall check reads it.
