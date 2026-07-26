# ccenv

**The missing operating environment for Claude Code.**

Claude Code is an excellent coding agent, but it has no long-term memory, no continuity between sessions, no awareness of previous work, and no built-in way to coordinate multiple instances.

ccenv fills those gaps.

It layers project awareness, persistent memory, automatic session handoff, and context monitoring on top of Claude Code while remaining lightweight and composable.

The result is a continuous AI development environment where work survives context exhaustion, sessions resume with minimal friction, and long-running projects can evolve across days, weeks, or months instead of ending when a conversation does.

---

## What's Included

| Component   | What it does                                              | MCP name   |
|-------------|-----------------------------------------------------------|------------|
| `ccproject` | Three-layer project awareness skill (constitution → subsystem docs → structural map) + global `~/.claude/CLAUDE.md` snippet | — |
| `ccmemory`  | Persistent file-backed memory with FTS5 index, autoinstalled hooks, MCP server | `ccmemory` |
| `ccusage`   | Real-time context-window + rate-limit usage as an MCP tool and statusline | `ccusage` |
| `ccloop`    | Relay-loop wrapper that hands work between fresh Claude Code sessions as context fills | — |

Each component still lives in its own subdirectory and has its own
`README.md`, `pyproject.toml`, and tests. Top-level `install.sh`
delegates to each component's own installer where one exists, and falls
back to `pip3 install --user <path>` (non-editable) for the rest.

### Removed in v0.13.0

Three components are gone from the bundle:

| Component    | Status                          |
|--------------|---------------------------------|
| `ccprospect` | Retired                         |
| `ccinsight`  | Retired                         |
| `ccteam`     | Moved to its own repository     |

They installed hooks, MCP servers, skills and per-project state. As of
v0.16.0 `./install.sh` detects anything they left behind and removes it
for you, scoped to those components only — see
[Upgrading](#upgrading). Run `./install.sh --check-retired` to see what
(if anything) is still on your box, without changing a thing.

## Install

```sh
git clone https://github.com/sshoecraft/ccenv.git
cd ccenv
./install.sh
```

Per-component options:

```sh
./install.sh --skip ccloop        # skip a component (repeatable)
./install.sh --only ccmemory      # install only listed components (repeatable)
./install.sh --no-overlays        # skip the overlay scan
./install.sh --check-retired      # report retired-component residue, change nothing
./install.sh --no-retired-cleanup # leave retired-component residue in place
./install.sh -h                   # full help
```

Re-running is idempotent. Each sub-installer checks its own state; MCP
registrations use `claude mcp get <name>` to detect prior installs;
`~/.claude/CLAUDE.md` overlay blocks are stripped and re-applied so stale
content self-heals.

## Upgrading

**From any version:**

```sh
git pull
./install.sh
```

Since v0.16.0 the installer handles the retired components itself. It
checks four independent signals per component — console script in the
`--user` bin, hook entry in `settings.json`, MCP registration in
`~/.claude.json`, `dist-info` in the `--user` site — and when it finds
any, it runs `./uninstall.sh` scoped with `--only` to exactly those
components before installing anything. A clean box does no work at all.

That matters because an install otherwise only ever *adds*: it cannot
remove a hook, MCP registration or skill belonging to a component the
bundle no longer ships, so upgrading in place used to leave
`ccprospect`, `ccinsight` and `ccteam` fully wired into every session —
SessionStart hooks still firing, MCP servers still registering, and their
binaries still present so nothing failed loudly.

Check without changing anything:

```sh
./install.sh --check-retired
```

Per-project `.ccprospect/`, `.ccinsight/` and `.ccteam/` state dirs are
**kept** by this automatic pass. Add `--purge-retired-state` to have them
archived and removed too, or run the uninstaller yourself for a full
teardown:

```sh
./uninstall.sh          # removes EVERYTHING ccenv has ever installed
```

`uninstall.sh` knows about every component ccenv has ever shipped,
including the three that were removed, which is exactly why it has to run
from the NEW checkout rather than the old one.

What it will not touch: `.ccmemory/` directories (committed repo content
that travels with your repos) and `.ccloop/` run state, which it lists
rather than deletes. Per-project `.ccprospect/`, `.ccinsight/` and
`.ccteam/` state is tarred into `~/ccenv-uninstall-<stamp>/` before
removal, and every file it rewrites is backed up beside the original.

Preview it first if you like — it changes nothing:

```sh
./uninstall.sh --dry-run
```

See [docs/uninstall.md](docs/uninstall.md) for the full removal matrix.

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
`ccmemory`, `ccusage`. Overlay MCP servers default to their subdir name
unless overridden.

## Requirements

- Python 3.11+ (3.12 verified)
- `pip3`
- Claude Code CLI on `PATH` for MCP / hook registration steps (the
  installer falls back gracefully and prints warnings if it can't find
  `claude`)

## License

MIT — see [LICENSE](LICENSE).
