---
name: claude-code-does-not-reap-task-output-files
description: Claude Code does NOT delete a Bash background task's .output file when the command finishes — it persists for the whole session. Presence != liveness.
metadata:
  type: project
tags: [ccloop, claude-code, background-tasks, hooks]
---

Empirically confirmed 2026-06-16 on Linux (uid 1000). Claude Code stores each Bash-background task's stdout/stderr at `/tmp/claude-<uid>/<slug>/<session-id>/tasks/<task-id>.output`. **The harness does NOT delete that file when the command completes.** It lingers for the entire session and beyond — old mxfs sessions held 7 and 41 stale `.output` files; a 5-file session matched the exact 5 long-finished task IDs.

There is **no on-disk running-state registry** — the tasks dir contains only `.output` files. The only way to tell a running task from a finished one on disk is liveness of the writer process:

- **Running** task → its `.output` is held open by a live process (visible as a `/proc/<pid>/fd/<n>` symlink target; verified: live=holders>0).
- **Finished** task → 0 holders, file persists.

**Why it matters:** ccloop's Stop-hook wait gate (`keepgoing._pending_background_task_count`) originally counted bare file *presence* as "command still running," on the false belief that "the harness reaps the file in seconds." It does not. So once any background command had ever run in a ccloop session, its orphaned `.output` re-fired the gate on every subsequent Stop — the session could neither relay nor exit, emitting "N background command(s) still running" forever until the context wall. This is exactly the false positive seen in the mxfs run (session c3bb9e30, 5 dead files).

**Fix (ccloop 0.5.1):** the gate now counts an `.output` only when a live process holds it open — `/proc/<pid>/fd` scan, no subprocess, only on Stop, short-circuited. Non-procfs platforms (macOS) fall back to an mtime freshness window (`STALE_OUTPUT_SECONDS=90`) so a stale file can't fire the gate indefinitely. Verified against the real wedged session: 5 files present → 0 counted.

**How to apply:** any tool that wants to know whether a Claude Code Bash background task is *still running* must check writer liveness (procfs fd holder, or mtime), NOT file presence. Do not assume the harness cleans up `tasks/*.output`.

Corrects the "false positives are momentary / recoverable" claim in [[ccloop-stop-hook-return-0-kills-session]].
