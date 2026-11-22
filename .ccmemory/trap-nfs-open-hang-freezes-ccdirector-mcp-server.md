---
name: trap-nfs-open-hang-freezes-ccdirector-mcp-server
description: A ccdirector worker MCP server on the /src NFS share froze in D state (wchan nfs_set_open_stateid_locked); its single event loop stopped, tools went…
metadata:
  type: project
---

Observed 2026-11-18 on the mxfs ccdirector run (ccdirector 0.17.1, kernel 6.8.0, /src = 192.168.1.8:/src nfs4 vers=4.1 hard nconnect=8).

- One worker's `ccdirector mcp` process went into uninterruptible sleep (`STAT D`,
  `wchan nfs_set_open_stateid_locked`) during a file open on the project's
  `.ccdirector/` (state.json / work.lock / beat file all live there, on NFS). The
  other three servers on the same files kept working. It was the only D-state
  process on the box.
- The server is single-threaded asyncio with synchronous file I/O, so one hung
  NFS open froze every tool: the blocked `acquire` stopped heartbeating (beat file
  mtime stopped), Claude Code aborted it with "sent no response or progress for
  1818s", and the next instant `resources` call was aborted the same way. The
  worker carried on without its ccdirector tools.
- A D-state process cannot be killed until the NFS op returns.
- How to see it: `<worker>.beat` mtime stale while other beats are current;
  `/proc/<pid>/wchan` of the worker's `ccdirector.cli mcp` (find it by
  `CCDIRECTOR_WORKER=` in `/proc/<pid>/environ`).

Related facts measured the same day with tests/probe_mcp_timeout.py:
- Claude Code (headless) DOES send `_meta.progressToken` on tools/call.
- A per-server `"timeout"` (ms) in the MCP config is a HARD wall-clock limit:
  progress notifications do not reset it, and on expiry Claude sends
  `notifications/cancelled`. Do not use it to "allow longer waits".
- Without a per-server timeout, progress does reset the 1800 s silence limit
  (acquire/run calls lasted over 2 h with heartbeats).
