---
name: mcp-alwaysload-blocks-startup
description: MCP startup is non-blocking by default (tools deferred behind ToolSearch); alwaysLoad:true blocks startup until a server connects. Set via add-json (…
metadata:
  type: reference
tags: [mcp, claude-code, install.sh, ccloop, startup-race]
---

# MCP alwaysLoad blocks session startup until a server connects

## Problem it solves
Claude Code loads MCP servers **non-blocking by default**. With tool-search on
(the default) each MCP server's tools are DEFERRED behind `ToolSearch` and the
server connects in the background — so the model's FIRST turn can begin before
the tools register. In a ccloop **TUI** session this races the required first
actions (ccmemory `memory_list()`, ccteam claim-before-edit): they silently run
without their tools. Not a ccloop bug; NOT fixable from ccloop's stream-json
parsing (the TUI emits no stream-json).

## Fix: `alwaysLoad: true`
A field on a server's config object in `~/.claude.json` (`mcpServers.<name>`).
Confirmed in the 2.1.207 binary — the tool-deferral fn is
`if(alwaysLoad===true) return false /*eager*/; ... if(isMcp) return true /*deferred*/`.
Schema doc: *"When true, all tools from this server are always included in the
prompt and never deferred."* Because the tools must be present when the first
prompt is built, Claude **BLOCKS startup until that server connects** (~5s/server
cap; a dead server can't hang you). Claude-native → works in BOTH TUI and
headless, no model compliance needed.

## How to set it (all non-obvious)
- There is **NO `claude mcp add` flag** for alwaysLoad.
- `claude mcp get` / `claude mcp list` do **NOT** surface alwaysLoad → to check
  idempotently you must read `~/.claude.json` directly.
- Set via `claude mcp add-json -s user <name> '<entry-json-with-alwaysLoad>'`.
  `add-json` **refuses to overwrite** an existing server ("... already exists in
  user config"), so `claude mcp remove -s user <name>` FIRST — same heal pattern
  as `register_mcp`. Prefer this over a python rewrite of `~/.claude.json`:
  claude's JSONC editor edits surgically, a python `json.dump` reformats the
  whole 256KB file.

## In ccenv (v0.6.0)
`install.sh:enable_always_load()` marks **ccmemory** and **ccteam** (the servers
a session needs at turn 1). Runs AFTER `register_mcp` so a heal-triggered
re-register (which drops the flag) re-applies it. Idempotent. `ask_*` left
deferred on purpose — eager-loading just spends prompt tokens on tools that
shouldn't be routinely called. The 3 claude.ai HTTP servers show "Needs
authentication" and never connect unattended — never gate on them.

## Rejected alternatives
- **stream-json `init` event** carries `mcp_servers:[{name,status}]` but only
  headless; it's telemetry, not a gate (can't pause the model between init and
  turn 1).
- **Prompt-preamble self-check** (`claude mcp list` + wait loop) works in both
  modes but is soft (behavioral, not mechanical) — a backstop at best.
- **SessionStart hook** blocking on `claude mcp list` works in both modes (it DOES
  block the first turn) but adds per-start latency (full health check spawns
  every server) and lives in ccloop.

## Related env vars
`MCP_TIMEOUT` = server startup timeout ms (default 30s). `MCP_TOOL_TIMEOUT` =
tool exec timeout. `--strict-mcp-config` = only use `--mcp-config` servers (a
config gate, NOT a startup blocker).
