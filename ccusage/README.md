# ccusage-mcp

MCP server that exposes Claude Code's own context-window and rate-limit usage
to Claude Code. Lets the assistant answer "how many tokens do I have left?"
with a real number rather than a guess.

## Install

```sh
python3 install.py
```

The installer is UID-aware:

- **non-root** — `pip3 install --user .` (binaries in `~/.local/bin/`),
  `statusLine` written to `~/.claude/settings.json`, MCP server registered via
  `claude mcp add --scope user` (writes to `~/.claude.json`)
- **root** — system-wide `pip3 install .` (binaries in `/usr/local/bin/`),
  `statusLine` written to `/etc/claude-code/managed-settings.json`, MCP server
  registered in `/etc/claude-code/managed-mcp.json` (both apply to all users)

Idempotent: re-running skips entries that are already correct, warns on
conflicts. Requires `python3` and `pip3` (and the `claude` CLI, for the
non-root MCP registration step).

### Manual install

If you'd rather wire it up yourself:

```sh
pip3 install .                                  # exposes ccusage-mcp + ccusage-statusline
claude mcp add --scope user ccusage ccusage-mcp # user scope = available in all projects
```

Then point your Claude Code `statusLine` at `ccusage-statusline` (in
`~/.claude/settings.json` or `/etc/claude-code/managed-settings.json`):

```json
{
  "statusLine": {
    "type": "command",
    "command": "/full/path/to/ccusage-statusline"
  }
}
```

`ccusage-statusline` reads Claude Code's status JSON on stdin, writes it to
the cache file the MCP server reads, and prints the formatted status line on
stdout. If you have an existing statusline you want to keep, drop this block
into the top of it instead — same effect:

```python
# in any python script that gets the JSON on stdin
import json, os, sys
from pathlib import Path
raw = sys.stdin.read()
sid = (json.loads(raw).get("session_id") if raw.strip() else None) or f"uid-{os.getuid()}"
base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
cache = Path(base) / "ccusage" / f"{sid}.json"
cache.parent.mkdir(parents=True, exist_ok=True)
tmp = cache.with_name(f".{cache.name}.{os.getpid()}")
old = os.umask(0o077)
try:
    tmp.write_text(raw)
finally:
    os.umask(old)
os.replace(tmp, cache)
```

## Tools

- **`get_context_usage`** — human-readable summary. Context tokens used vs
  total, percent, remaining; 5-hour and 7-day rate-limit usage and reset times;
  cache age.
- **`get_context_usage_raw`** — the raw JSON Claude Code passed to the
  statusline, plus cache age in seconds. Use when you need exact numbers or
  fields the summary omits.

## Architecture

```
Claude Code turn ends
      |
      v
ccusage-statusline  (receives JSON on stdin from Claude Code)
      |
      |--> writes status line to stdout
      |--> writes JSON to $XDG_STATE_HOME/ccusage/<session-id>.json (atomic, 0600)
                                  ^
                                  |
ccusage-mcp server (stdio)  ------+   (reads the most-recently-written file)
      |
      v
get_context_usage  ->  formatted string returned to Claude
```

The MCP server never receives the context JSON directly — Claude Code only
pipes that to the statusline. So the statusline acts as the data source: it
writes the JSON atomically to a **per-session** cache file (mode 0600), keyed
by Claude Code's `session_id`. The server doesn't know its own session id, so
it reads the most-recently-written file. ccloop's hooks, which DO know their
session id, read their own session's file directly.

Cache freshness ≈ "time since the last statusline render", which is roughly
once per turn. The cache file's mtime is reported as `cache age` in the tool
output so Claude can judge staleness.

## Files

- `server.py` — MCP server (via the `ccenvmcp` FastMCP shim), defines the two tools (`ccusage-mcp`)
- `statusline.py` — Claude Code statusline + cache writer (`ccusage-statusline`)
- `paths.py` — shared cache-path resolution, imported by both
- `run_server.py` — stdio entrypoint for running the MCP server from source
- `install.py` — UID-aware installer
- `pyproject.toml` — packaging, exposes both console scripts
- `LICENSE` — MIT

## Cache file path

`$XDG_STATE_HOME/ccusage/<session-id>.json` (default
`~/.local/state/ccusage/`) — one file **per session**, mode 0600, written
atomically (tmpfile in same dir + `mv`). Files not written within 2 days are
pruned on the next statusline render so the dir stays bounded.

Previously this was a single per-UID file in `/tmp`. That was clobbered by any
concurrent same-UID Claude Code session — whichever rendered its statusline
last owned the file, so every other session's reader saw a foreign
`session_id` and silently bailed. The per-session file removes that race.

## Versions

- **0.3.0** — Cache moved from a single per-UID `/tmp` file to a per-session
  file under `$XDG_STATE_HOME/ccusage/<session-id>.json` (default
  `~/.local/state/ccusage/`), pruned after 2 days. Concurrent same-UID
  sessions no longer clobber each other's usage. The MCP server reads the
  most-recently-written file; the statusline keys the file by `session_id`.
- **0.1.1** — `install.py` stages the source into a local tmpdir before
  invoking pip, so the wheel build never runs on NFS/SMB/AFP mounts where
  macOS auto-creates `._*` AppleDouble sidecars (fixes "multiple .dist-info
  directories found" wheel-build failure). The `statusLine` setting is now
  force-replaced rather than warned-about-on-conflict in both install modes
  (user → `~/.claude/settings.json`, root → `/etc/claude-code/managed-settings.json`),
  so re-running the installer always points statusLine at the bundled
  `ccusage-statusline`.
- **0.1.0** — Initial release. Two tools, file-cache data source.

## Known limitations

- If the statusline has not run yet in a session, the cache is missing or
  stale and the tool returns an error / old `cache age`. First tool call in a
  brand-new session may need to wait one turn.
- Bound to the statusline data shape Claude Code provides. If Anthropic
  renames `context_window.used_percentage` etc., the formatter needs updating.
- The MCP server is not told its own `session_id`, so it reports the
  most-recently-written per-session cache. With a single active session that is
  that session; with several concurrent sessions under the same UID it is
  whichever rendered its statusline most recently. (ccloop's hooks are not
  affected — they read their own session's file by id.)

## License

MIT — see `LICENSE`.
