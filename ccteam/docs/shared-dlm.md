# Shared-filesystem DLM (broker-free, peer-to-peer)

## Why this exists

ccteam's default coordination routes everything through NATS: the lock
table (KV with CAS) **and** replication (event stream + object store).
When NATS is unreachable the node degrades to standalone and locking is
dead — even when peers sit on the same LAN sharing a filesystem.

On a **shared filesystem** (multiple machines, same mounted FS), the
bytes are already common to every node, so replication is redundant. The
only thing left to coordinate is **mutual exclusion** — a lock table.
That does not need a broker. This backend provides it peer-to-peer, so a
shared-FS cluster coordinates with **zero NATS dependency**: a NATS
outage cannot disable coordination because there is no NATS in the path.

## Selecting it

`dlm_backend` (`CCTEAM_DLM_BACKEND`): `auto` | `nats` | `p2p`.

- `auto` (default): resolves to `p2p` when `shared_filesystem`
  (`CCTEAM_SHARED`) is true, else `nats`.
- `p2p`: force the broker-free backend.
- `nats`: force the broker backend (replication + KV locks).

`dlm_port` (`CCTEAM_DLM_PORT`): TCP listener port for the p2p backend.
`0` (default) lets the OS pick an ephemeral port, which is advertised to
peers via discovery.

## Model

Single-master, lowest-node-ID election — the same model proven in MXFS
(`/src/mxfs/docs/dlm-protocol.md`):

- The live member with the lowest `node_id` is the **master** for all
  paths and holds the authoritative lock table.
- Non-master nodes forward `LOCK_REQ` to the master and block until a
  `LOCK_GRANT` / `LOCK_DENY` message returns.
- On a membership change the master is recomputed. The new master seeds
  its table from its own held locks; each survivor pushes its self-known
  holds via `TABLE_SYNC`.

Membership comes from the existing UDP-multicast discovery
(`discovery.py`), which already tracks peers, ages them out on missed
announces, and now carries each peer's `dlm_port`. No NATS is involved.

## Components

| File | Role |
|---|---|
| `p2p_transport.py` | One TCP listener per node + a best-effort `send`. Length-prefixed JSON frames. Message-passing, not RPC. |
| `p2p_dlm.py` | `P2PDlm` — election, master lock table, request forwarding, promotion, failover. Mirrors the `dlm.DLM` surface and reuses its `LockMode`/`Holder`/`LockState`/`ClaimResult`/`compatible`/`path_key`. |
| `discovery.py` | Membership + `dlm_port` propagation. |
| `node.py` | `start_p2p()` wires transport + DLM + discovery; no overlay/watcher/consumer. `on_peer_event_p2p` drives `on_membership_change` + `purge_node`. |

## Wire messages

All frames are `{type, ...}`; replies correlate by `req_id` at the DLM
layer (so a blocking claim waits on a future, not an open socket).

- `LOCK_REQ` {req_id, path, mode, node_id, hostname, pid} → master
- `LOCK_GRANT` / `LOCK_DENY` {req_id, path, status, prior} → requester
- `LOCK_RELEASE` {path, node_id} → master
- `LOCK_CANCEL` {req_id, path, node_id} → master (requester timed out)
- `FORCE_REQ` {req_id, path, mode, node_id, hostname, pid} → master
- `TABLE_SYNC` {node_id, hostname, pid, holds:[{path,mode}]} → new master
- `HELD_QUERY` / `HELD_RESP` {req_id, path / holders} (status holders)

## Lock request flow

**Local master path** (`_local_claim`): grant immediately if compatible
and no waiters ahead (FIFO-fair); otherwise queue a `Waiter` with a local
future and await it. Promotion on release/​purge resolves the future.

**Remote master path** (`_remote_claim`): register a pending future, send
`LOCK_REQ`, await the grant/deny within the remaining timeout. On timeout
send `LOCK_CANCEL` and report the current holders (best-effort
`HELD_QUERY`). A `not_master`/`retry` reply re-resolves the master and
retries within the deadline. A grant that arrives after giving up is
released back so the master doesn't leak the lock.

## Failover sequence

1. Discovery times out the dead node → `on_peer_event_p2p("leave")`.
2. `on_membership_change` recomputes the master.
3. In-flight pending requests and local waiters are failed with `retry`;
   `claim()` re-enters against the new master.
4. New master seeds its table from its own holds; survivors send
   `TABLE_SYNC`.
5. If the local node is master, `purge_node` drops the departed node's
   holds and promotes any unblocked waiters.

## What works vs. NATS mode

Available in p2p mode: `claim`, `release`, `peers`, `status`,
`force_claim`. Unavailable (return "unavailable in p2p mode"):
`recent_changes`, `checkpoint`, `resync`, `verify` — all of these depend
on the NATS overlay/object-store, which p2p mode does not run (there is
no diff log to report and no second copy to verify against on a shared
FS).

## Scope vs. MXFS (deliberately smaller)

ccteam has a handful of nodes, low contention, and tolerates the rare
race (last-writer-wins on a shared file). A filesystem cannot tolerate a
bad grant (data corruption); ccteam can. So this backend intentionally
**omits**: journal recovery, cache-coherency BASTs, the 6-mode matrix,
per-resource mastering, and the epoch-mismatch hardening MXFS needed.
Correctness-under-normal-operation is the bar.

## History

- 2026-06-13: Initial implementation (ccteam 0.2.0). New `p2p_transport`
  + `p2p_dlm`; discovery carries `dlm_port`; `node.start_p2p`; config
  `dlm_backend`/`dlm_port`; MCP/IPC overlay coupling relaxed so lock ops
  work without the overlay. Unit + loopback integration tests added (no
  NATS required).
