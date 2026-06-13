# ccteam architecture

## Three layers

```
┌─────────────────────────────────────────────────┐
│ Claude Code instance                            │
│   ↓ MCP stdio                                   │
│ ccteam-mcp  (one per Claude Code session)  │
│   ↓ Unix socket (local RPC)                     │
│ ccteam CLI  (invoked by hooks)             │
└─────────────────────────────────────────────────┘
          ↓ UDP multicast 239.66.83.2:7500
          ↓ NATS client (user-supplied URL)
┌─────────────────────────────────────────────────┐
│ NATS JetStream server                           │
│   KV bucket (locks)                             │
│   event stream (overlay)                        │
│   object store (file contents)                  │
└─────────────────────────────────────────────────┘
```

1. **Discovery** — UDP multicast announces peers in the same cluster.
   Works across loopback (multiple Claudes on one host) and LAN.
   See `ccteam/discovery.py`.
2. **NATS JetStream** — user-run external broker. Does all peer-to-peer
   traffic in the default (non-shared) mode. No direct TCP between peers
   — **except** in the shared-filesystem p2p backend (see below).
3. **MCP server** — one per Claude Code session; exposes MCP tools and
   a Unix socket for the hook CLI.

## Coordination backends

The lock table has two interchangeable backends, selected by
`dlm_backend` (`auto` resolves to `p2p` when `shared_filesystem` else
`nats`):

- **`nats`** — `dlm.DLM` over a NATS KV bucket (CAS). Pairs with the
  overlay/object-store for replication. Required when peers do **not**
  share a filesystem.
- **`p2p`** — `p2p_dlm.P2PDlm`: a broker-free, single-master
  (lowest-node-ID) election DLM over per-node TCP (`p2p_transport.py`),
  with membership from discovery. For shared filesystems, where
  replication is redundant, so **no NATS, no overlay, no watcher**. This
  is the one place ccteam opens direct TCP between peers. See
  `docs/shared-dlm.md`.

Both backends expose the same method surface
(`claim`/`release`/`held_by`/`force_claim`/`purge_node`) returning the
same `ClaimResult`/`LockState`/`Holder` dataclasses, so the MCP and IPC
layers are backend-agnostic.

## Module overview

| Module | Role |
|---|---|
| `project_root.py` | Walk up from cwd to find the project. No git contents read. |
| `cluster.py` | Derive cluster id from root basename; mint a per-session node id. |
| `config.py` | Load config from user/project files + env. |
| `manifest.py` | Compute `{path: sha256}` for tracked files. |
| `checkpoint.py` | Read/write `.ccteam/checkpoint.json` + `manifest.json`; take a new checkpoint; rotate the overlay stream. |
| `discovery.py` | UDP multicast sender + receiver; peer table with liveness timeout. |
| `nats_client.py` | Connect, ensure stream/KV/object-store, derive subject names. |
| `dlm.py` | CAS-based lock table over NATS KV. Single-key-per-path, multi-holder value for SHARED. |
| `p2p_transport.py` | Per-node TCP listener + best-effort send; length-prefixed JSON frames. Used only by the p2p backend. |
| `p2p_dlm.py` | Broker-free election DLM (shared-filesystem). Lowest-node-ID master, request forwarding, failover via `TABLE_SYNC`. Mirrors the `dlm.DLM` surface. |
| `overlay.py` | Snapshot, diff compute, event publish, event apply, replay. |
| `local_ipc.py` | Unix-socket JSON line protocol for hook ↔ server. |
| `watcher.py` | Continuous filesystem watcher (`watchfiles`); publishes DIFF/CREATE/DELETE for edits that bypass Claude's tools (sed, redirects, git pull, etc). |
| `node.py` | Composition root: wires identity, NATS, discovery, DLM, overlay, watcher. |
| `mcp_server.py` | FastMCP stdio server (via the stdlib `ccenvmcp` shim, not the official `mcp` SDK — keeps the 3.9 floor); also hosts the local IPC listener. |
| `cli.py` | `ccteam` CLI + hook shim. |

## Key invariants

- Local filesystem state = `checkout(checkpoint)` + `replay(overlay 0..N)`.
- A new peer joining must match the cluster's checkpoint number + id +
  manifest hash, or it refuses to auto-apply events.
- The cluster's view of "who holds what" lives in a NATS KV bucket with
  CAS semantics — no node-local lock table, no master election.
- When a peer times out in discovery, surviving peers call
  `DLM.purge_node` to release its locks.

## Replication: hook path vs. watcher path

Two independent mechanisms publish overlay events:

1. **Hook path** (Edit/Write/NotebookEdit/MultiEdit): PreToolUse claims
   the lock and snapshots pre-content; PostToolUse computes a diff and
   publishes. Provides mutual exclusion and immediate replication.
2. **Watcher path** (everything else — `sed -i`, shell redirects, `git
   pull`, editor-outside-Claude): background `watchfiles` loop detects
   the change, hashes it, publishes a DIFF/CREATE/DELETE. No mutual
   exclusion (we can't parse shell to know which path will change), but
   replication is covered.

Both paths share an in-memory hash cache on `Overlay` so Claude's own
edits don't get double-published — when the hook records the post-hash,
the watcher wakes up, finds the hash already matches the cache, and
skips.

## Platform scope

Linux and macOS only. `watchfiles` handles both via inotify/FSEvents,
and Python's asyncio supports Unix domain sockets natively on both.
Windows is intentionally out of scope for v1.

## What ccteam deliberately doesn't do

- Read `.git/` contents.
- Solve merge conflicts. If two peers diverge without using claims, the
  DLM does not magically reconcile; the `verify` MCP tool surfaces drift.
- Auto-launch or supervise nats-server. Users run their own.
- Provide mutual exclusion for shell-driven edits (sed, redirects, etc).
  Those replicate via the watcher but don't take DLM claims.

## History

- 2026-04-22: Initial scaffold and module-by-module implementation.
  Foundational modules (project_root, cluster, config, manifest,
  checkpoint, discovery) tested standalone. NATS-dependent modules
  (nats_client, dlm, overlay) have unit tests for pure logic and
  integration tests gated on `CCTEAM_NATS_URL`.
