# ccteam

Coordination layer for multiple [Claude Code](https://claude.ai/code)
instances editing the same project.

When two or more developers (or two Claude sessions on one machine) work
on the same codebase at the same time, ccteam keeps them from
stepping on each other: one holds an exclusive lock on a file, others
block or get told who's editing it. Completed edits propagate to every
peer's filesystem through an event log, so all checkouts stay in sync in
real time. Edits outside Claude (shell commands, manual editors, `git
pull`) are replicated too via a filesystem watcher.

Exposed to Claude Code as an MCP server, with plug-in hooks that
auto-claim on every Edit/Write tool call so the coordination is
deterministic — not dependent on Claude remembering to call the tool.

## Architecture

```
Developer laptop                   Alice's laptop            Bob's laptop
┌─────────────────────────────┐
│ Claude Code                 │
│   ↓ MCP stdio               │
│ ccteam-mcp             │ ←──── NATS TCP ────→ [NATS server] ←────→
│   ↑ Unix socket             │
│ ccteam CLI ◄── hook    │
└─────────────────────────────┘
         ↑ UDP multicast :7500 discovery
```

Three layers, each doing one thing:

1. **Discovery** — UDP multicast on `239.66.83.2:7500` finds peers in
   the same cluster (loopback + LAN).
2. **NATS JetStream** (user-run) — KV bucket holds the lock table (CAS
   semantics), an event stream carries the overlay (claim / release /
   diff / create / delete / rename), and an object store holds file
   contents for late joiners and `resync`.
3. **MCP server** — one per Claude Code session. Exposes coordination
   tools to Claude and hosts a local Unix socket so the hook CLI can
   claim/release before and after tool calls.

## Requirements

- **Linux or macOS.** Windows is not supported — run in WSL if you must.
- Python 3.9+ (MCP transport is the bundle's stdlib `ccenvmcp` shim, not the
  official `mcp` SDK, so no 3.10 floor).
- A reachable `nats-server` with JetStream enabled (run your own; the
  package does not ship or auto-launch one).
- Claude Code itself, for the MCP integration.

## Quick start

```bash
# 1. Get NATS running somewhere (local for dev).
brew install nats-server   # or grab a release binary
nats-server -js &

# 2. Install the package.
git clone https://github.com/sshoecraft/ccteam.git
cd ccteam
pip3 install --user .

# 3. Point ccteam at your NATS server.
export CCTEAM_NATS_URL=nats://localhost:4222

# 4. Install the Claude Code plugin (wires up the MCP server + hooks).
claude plugin install ./plugin
```

Open Claude Code in a project. On first run, ccteam founds a new
cluster from your current filesystem state and writes
`.ccteam/checkpoint.json` + `.ccteam/manifest.json`. Commit
those files to your repo so teammates on other machines receive the
cluster definition through their normal `git pull`.

## Configuration

Precedence (highest first): explicit arg → env var →
`.ccteam/config.json` in the project → `~/.ccteam/config.json`
→ built-in defaults.

| Env var | Default | Description |
|---|---|---|
| `CCTEAM_NATS_URL` | `nats://localhost:4222` | NATS server URL |
| `CCTEAM_DISCOVERY_PORT` | `7500` | UDP multicast port |
| `CCTEAM_CLAIM_TIMEOUT_MS` | `30000` | Default claim wait |
| `CCTEAM_MAX_DIFF_SIZE` | `1048576` | Max inline diff payload (1 MB) |
| `CCTEAM_SHARED` | `false` | Shared-filesystem mode (don't apply peer diffs locally) |
| `CCTEAM_DLM_BACKEND` | `auto` | Lock backend: `auto` \| `nats` \| `p2p`. `auto` → `p2p` when shared, else `nats` |
| `CCTEAM_DLM_PORT` | `0` | TCP port for the p2p DLM listener (`0` = ephemeral, advertised via discovery) |
| `CCTEAM_CLUSTER` | auto | Override cluster name |

## MCP tools

Exposed to Claude via the MCP server:

| Tool | Purpose |
|---|---|
| `peers` | Active peers in this cluster |
| `claim` | Lock one or more paths (blocking with timeout) |
| `release` | Drop claims |
| `status` | Self state + cluster checkpoint |
| `recent_changes` | Overlay events filtered by path |
| `checkpoint` | Take a new checkpoint, rotate the stream |
| `force_claim` | Preempt a held lock; logged with audit trail |
| `verify` | Rehash local files, report drift |
| `resync` | Overwrite a local file with cluster's authoritative content |

Same operations are available from the shell via `ccteam claim`,
`ccteam status`, `ccteam force-claim`, etc.

## How coordination happens

The plugin's PreToolUse hook fires before every `Edit / Write /
NotebookEdit / MultiEdit` tool call. It runs `ccteam hook pre`,
which takes an EXCLUSIVE lock on the file Claude is about to edit. If
another peer holds it, the call blocks up to 5 seconds (covering "the
other peer is just finishing"); if still held, the hook exits with a
structured message telling Claude who holds it — Claude surfaces that
to the user, who picks *wait longer* / *force* / *skip*.

PostToolUse fires after the tool completes. It publishes the diff
against the pre-edit snapshot and releases the lock. Peers subscribed
to the event stream receive the diff, verify the pre-hash matches their
local file, and apply the unified diff.

Shell-driven edits (`sed -i`, `>`, `mv`, editor outside Claude, `git
pull`) are not locked — we can't tell from a shell command what paths
will change — but they are *replicated* via a continuous filesystem
watcher running inside the MCP server. Hashes are deduped against
Claude's own edits so nothing double-publishes.

## Shared filesystem (broker-free, no NATS)

If your peers share a mounted filesystem (same LAN, same NFS/SMB mount),
replication is redundant — the bytes are already common to every node.
In that case ccteam can coordinate locks **without NATS at all**:

```bash
export CCTEAM_SHARED=true        # → dlm_backend auto-resolves to p2p
```

The p2p backend runs a broker-free, single-master (lowest-node-ID)
election DLM over direct TCP between peers, with membership from the
existing UDP-multicast discovery. A NATS outage cannot disable
coordination because there is no NATS in the path. The trade-off:
`recent_changes`, `checkpoint`, `resync`, and `verify` are unavailable in
this mode (they depend on the NATS overlay/object-store; on a shared FS
there is no diff log to report and no second copy to verify against).
`claim`, `release`, `peers`, `status`, and `force_claim` work as usual,
including the auto-claim Edit/Write hooks.

See [docs/shared-dlm.md](docs/shared-dlm.md) for the protocol, election,
and failover details.

## Checkpoints

ccteam never reads `.git/`. But the `.ccteam/checkpoint.json`
and `.ccteam/manifest.json` files are committed to your repo by
convention, which is how checkpoints travel between machines — git
pull, rsync, whatever. ccteam is unaware of the transport.

`checkpoint` MCP tool captures the current filesystem as a new
checkpoint, bumps the number, writes both files, and rotates the
overlay event stream so future events start from the new baseline.

## Working with git

ccteam is **not a git workflow tool**. It does real-time
filesystem replication: whatever Alice edits ends up on Bob's disk,
and vice versa. This is fundamentally incompatible with branch-based
isolation. Read that again.

**The rules:**

1. **All cluster members must be on the same branch.** If Alice is on
   `branch-steve` and Bob is on `branch-bob` while in the same cluster,
   Alice's changes will be replicated into Bob's working tree and
   committed to `branch-bob`, and Bob's will end up on `branch-steve`.
   When these branches eventually merge, you have overlapping,
   interleaved commits and a mess.
2. **Treat a cluster like a shared live editing session.** Think Google
   Docs, not git-flow. Everyone sees everyone's changes in real time.
   The cluster is a way to collaborate *within* a branch, not across
   branches.
3. **Git operations should be done with the cluster quiescent.** When
   someone is committing / pushing / pulling / switching branches, no
   one else should be editing. Coordinate verbally: "I'm going to
   commit, hold off for 30 seconds."
4. **Only one person merges to main.** Same reason — a merge is an
   externally-visible event and the next action (pulling main back
   into the feature branch on every peer) needs to be coordinated.
5. **Branch switches require leaving the cluster.** To switch branches:
   stop Claude Code (tears down your ccteam session), switch
   branch, restart. On restart your local state won't match the
   cluster — start a new cluster (`--new-cluster`) or rejoin the
   correct one.

**Recommended workflow:**

- Pick a feature branch. Everyone checks out that branch.
- Start ccteam cluster. Live-collaborate until the feature is
  ready.
- Cluster quiescent → one designated person commits the shared state
  and runs `checkpoint` (which writes the new checkpoint files).
- That same person pushes and opens a PR.
- When the PR merges, everyone leaves the cluster, pulls main,
  re-checks out the next feature branch, and starts a new cluster.

This is a deliberate design choice: we keep ccteam focused on the
"many developers, same branch, same moment" case (the case git does
poorly) and leave the "many branches, deliberate isolation" case to git
(which does it well).

## Limitations

- **All cluster members must be on the same git branch.** ccteam
  replicates filesystem changes, which is incompatible with branch
  isolation. See the "Working with git" section above.
- **Bash-driven edits don't have mutual exclusion.** Only replication.
  If two peers `sed -i` the same file at the same time, last writer
  wins. Coordinated edits must go through Claude's Edit/Write tools.
- **Checkpoint matching is byte-exact.** Line-ending differences
  (CRLF vs LF) between platforms produce mismatches. Configure git
  consistently across the team.
- **Case-sensitivity.** Mixed case-sensitive/case-insensitive
  filesystems can see phantom divergence. Edge case.
- **No peer-to-peer secrets for NATS.** Use NATS TLS + auth if your
  NATS server is reachable from untrusted networks.

## Docs

- [docs/architecture.md](docs/architecture.md) — module-by-module design
- [docs/protocol.md](docs/protocol.md) — wire formats, subject layout,
  KV schema
- [docs/operator.md](docs/operator.md) — running NATS, configuration,
  troubleshooting

## Development

```bash
# Unit tests (no external deps).
pytest tests/

# Integration tests (require a live NATS).
CCTEAM_NATS_URL=nats://localhost:4222 pytest tests/test_integration_nats.py
```

## License

MIT — see [LICENSE](LICENSE).
