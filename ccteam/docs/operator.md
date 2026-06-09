# ccteam operator guide

## Platform

Linux and macOS only. Windows is not supported. Run ccteam in WSL
on Windows if you must.

## Running NATS

ccteam does not ship or auto-launch nats-server. Pick one:

- **Local dev:** `brew install nats-server && nats-server -js`. Config
  defaults to `nats://localhost:4222`, which ccteam also defaults to.
- **Team server:** run nats-server on a LAN-accessible host:
  ```
  nats-server -js -a 0.0.0.0 -p 4222
  ```
  Every developer sets `CCTEAM_NATS_URL=nats://<host>:4222`.
- **Docker:** `docker run -p 4222:4222 -p 8222:8222 nats:latest -js`.

JetStream must be enabled (`-js` flag). ccteam auto-creates its
stream, KV bucket, and object store on first use.

## Configuration

Precedence (highest first): explicit arg → env var → project
`.ccteam/config.json` → user `~/.ccteam/config.json` →
defaults.

```json
{
  "nats_url": "nats://localhost:4222",
  "discovery_port": 7500,
  "claim_timeout_ms": 120000,
  "max_diff_size": 1048576,
  "shared_filesystem": false,
  "log_level": "INFO",
  "cluster_name_override": null
}
```

Environment variables (all `CCTEAM_*`):

| Var | Field |
|---|---|
| `CCTEAM_NATS_URL` | nats_url |
| `CCTEAM_DISCOVERY_PORT` | discovery_port |
| `CCTEAM_CLAIM_TIMEOUT_MS` | claim_timeout_ms |
| `CCTEAM_MAX_DIFF_SIZE` | max_diff_size |
| `CCTEAM_SHARED` | shared_filesystem (bool) |
| `CCTEAM_CLUSTER` | cluster_name_override |
| `CCTEAM_LOG_LEVEL` | log_level |

## Installing the plugin

From a local clone:

```
claude plugin install ./plugin
```

This wires:
- MCP server `ccteam` (stdio, `ccteam-mcp`)
- PreToolUse hook on `Edit|Write|NotebookEdit|MultiEdit` → `ccteam hook pre`
- PostToolUse hook on the same matchers → `ccteam hook post`

`ccteam-mcp` and `ccteam` must be on `PATH`. Install the
Python package with `pip install .` from the project root, or add
`<project>/.venv/bin` to your PATH.

## Working with git

ccteam replicates filesystem changes across all cluster members in
real time. Git manages history via branch-based isolation. These two
models are fundamentally in tension. Do not try to get around the rule
below — you will lose work.

**Rule: all cluster members must be on the same branch.**

What goes wrong if you break the rule: Alice on `branch-steve` edits
`foo.py`. ccteam replicates the edit onto Bob's disk, where Bob
is on `branch-bob`. Bob's next commit on `branch-bob` now contains
Alice's change. Same thing happens in reverse. When `branch-steve` and
`branch-bob` eventually merge, git sees two commits with overlapping
edits and either produces conflicts or — worse — silently accepts both
versions of the same change. You cannot undo this without rolling back
commits.

**Recommended workflow:**

1. Pick a feature branch. Every cluster member checks it out.
2. Start or join the cluster. Edit collaboratively.
3. When the feature is ready and everyone has stopped editing, one
   designated person:
   - runs `checkpoint` via the MCP tool or CLI (captures shared state,
     rotates the overlay, writes new checkpoint files)
   - commits (including the updated `.ccteam/` files)
   - pushes and opens the PR
4. Everyone else pulls before making further changes.
5. When the PR merges, every cluster member:
   - stops Claude Code (tears down their ccteam session)
   - `git checkout main && git pull && git checkout -b next-feature`
   - restarts Claude Code (rejoins or founds a fresh cluster)

**Branch switches are cluster-leaving events.** You cannot switch
branches while ccteam is running — the local filesystem won't
match the cluster checkpoint anymore and incoming events will fail
their pre-hash check. Stop the MCP server, switch branch, restart.

**Who merges:** only one person. A merge moves main forward; everyone
else must pull before their next edit. Coordinate: "I'm merging,
everyone pause and pull in 30s."

## Distributing checkpoints

ccteam writes `.ccteam/checkpoint.json` and `.ccteam/manifest.json`
on checkpoint creation. Commit these files in your repository so peers
receive them through normal `git pull`. Add anything ccteam-specific
you want excluded to `.ccteam/ignore` (ccteam's own ignore
list; unrelated to `.gitignore`).

## Divergence handling

If a peer's local state doesn't match the cluster's checkpoint, startup
refuses with a clear message. Options:

- **Fetch newer checkpoint from somewhere** (usually `git pull`) and retry.
- **Start `ccteam-mcp --new-cluster`** — founds a new cluster from
  the local state, discarding the existing cluster's overlay.
- **Start `ccteam-mcp --bump-cluster`** — publishes local checkpoint
  as the new cluster checkpoint (when your local is newer).
- **Start `ccteam-mcp --accept-cluster`** — destructively overwrite
  local files with the cluster's state on replay.

## Shell-driven edits (sed, redirects, etc.)

Claude Code's `Bash` tool and any commands it runs (`sed -i`, `>`,
`>>`, `mv`, `rm`, editor-outside-Claude, `git pull`, etc.) **bypass**
the DLM — no claim is taken, no mutual exclusion is enforced for
Bash-driven writes. To still keep peers in sync, the MCP server runs a
filesystem watcher that hashes changed files and publishes overlay
events. Inotify/FSEvents are event-driven; idle cost is negligible.

If you have a very large project, tune `.ccteam/ignore` so
high-churn build dirs (`node_modules/`, `target/`, `dist/`, `build/`)
are excluded. Linux inotify has a per-user watch limit (default 8192)
— `sysctl fs.inotify.max_user_watches=524288` raises it.

## Troubleshooting

- `peers` returns empty after startup → check multicast: `tcpdump -i any
  udp port 7500`. LAN may block multicast; run with
  `CCTEAM_DISCOVERY_PORT=<port>` to try a different port or use a
  directly-accessible NATS URL so peers can coordinate even if UDP
  discovery fails.
- `claim` times out even on a quiet cluster → inspect the KV bucket
  with `nats kv ls ccteam-<cluster_id>-locks` to find stale entries,
  or restart nats-server to clear state.
- Hook blocks Claude Code too long → the default hook timeout is 5s
  (most uncontended claims resolve in milliseconds). If a peer is
  legitimately mid-edit the hook exits with a structured "held by X"
  message and Claude surfaces options to the user. Reduce further via
  the CLI if 5s still feels long.
- Watcher not picking up changes → on Linux, check `sysctl
  fs.inotify.max_user_watches`; on macOS, FSEvents coalesces under
  heavy load (rare in normal dev). Running `ccteam verify` rehashes
  and reports drift.
