# ccenv

Self-contained Claude Code env/harness. Bundles five Claude Code tooling
projects into one installable repo, and ships an overlay system so a host
or user can layer additional MCP servers and instructions on top without
forking.

## What's in here

| Component   | What it does                                              | MCP name   |
|-------------|-----------------------------------------------------------|------------|
| `ccproject` | Three-layer project awareness skill (constitution → subsystem docs → structural map) + global `~/.claude/CLAUDE.md` snippet | — |
| `ccmemory`  | Persistent file-backed memory with FTS5 index, autoinstalled hooks, MCP server | `ccmemory` |
| `ccusage`   | Real-time context-window + rate-limit usage as an MCP tool and statusline | `ccusage` |
| `ccloop`    | Relay-loop wrapper that hands work between fresh Claude Code sessions as context fills | — |
| `ccteam`    | Multi-instance coordination layer (filesystem replication via NATS JetStream, file-level locking, MCP tools + hooks) | `ccteam` |

Each component still lives in its own subdirectory and has its own
`README.md`, `pyproject.toml`, and tests. Top-level `install.sh`
delegates to each component's own installer where one exists, and falls
back to `pip3 install --user <path>` (non-editable) for the rest.

## Install

```sh
git clone https://github.com/sshoecraft/ccenv.git
cd ccenv
./install.sh
```

Per-component options:

```sh
./install.sh --skip ccteam        # skip a component (repeatable)
./install.sh --only ccmemory      # install only listed components (repeatable)
./install.sh --no-overlays        # skip the overlay scan
./install.sh -h                   # full help
```

Re-running is idempotent. Each sub-installer checks its own state; MCP
registrations use `claude mcp get <name>` to detect prior installs;
`~/.claude/CLAUDE.md` overlay blocks are stripped and re-applied so stale
content self-heals.

## Overlay system

`install.sh` scans three locations for **per-user / per-host** extensions
that should layer on top of the bundled components:

```
/usr/local/ccenv        — system-wide overlay
~/.config/ccenv         — per-user overlay
<this script's dir>     — bundled (only scanned for MCP subdirs, not CLAUDE.md)
```

In an overlay directory, the installer looks for:

- **`CLAUDE.md`** — appended to `~/.claude/CLAUDE.md` inside a
  `# [CCENV OVERLAY: <path>]` … `# [/CCENV OVERLAY: <path>]` marker
  block. Re-runs strip stale blocks and re-merge eligible ones.
  *(System and user dirs only — never the bundled dir.)*
- **`<subdir>/pyproject.toml`** — pip-installed (`--user`) and registered
  as a user-scope MCP server. Default registration: name and command both
  default to the subdir name. Override with `<subdir>/.ccenv-mcp.json`:

  ```json
  {"name": "myname", "command": "bin", "args": ["--flag"], "scope": "user"}
  ```

This lets you add custom MCP servers on a single laptop, or distribute
them via `/usr/local/ccenv` on a shared box, without touching the bundled
component tree.

## MCP server naming

All MCP servers register at user scope under a `cc<short>` convention:
`ccmemory`, `ccusage`, `ccteam`. Overlay MCP servers default to their
subdir name unless overridden.

## Requirements

- Python 3.11+ (3.12 verified)
- `pip3`
- Claude Code CLI on `PATH` for MCP / hook registration steps (the
  installer falls back gracefully and prints warnings if it can't find
  `claude`)
- `nats-server` with JetStream — **only** if you want ccteam's
  cross-instance coordination. Without it, ccteam boots in standalone
  mode and a SessionStart hook surfaces a one-line notice in any
  `.ccteam/`-bootstrapped project so you know sync is off.

## License

MIT — see [LICENSE](LICENSE).
