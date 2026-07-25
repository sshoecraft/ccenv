---
name: mcp-alwaysload-blocks-startup
description: CORRECTED: alwaysLoad is a 5s per-TIER timeout that PROCEEDS degraded, not a barrier. SessionStart hooks run before init, so no hook can gate on MCP…
metadata:
  type: reference
tags: [mcp, claude-code, install.sh, ccloop, ccmemory, startup-race]
---

# MCP startup readiness — what alwaysLoad actually does

**CORRECTION (2026-07-24, verified against binary 2.1.219).** The earlier
version of this note said alwaysLoad "BLOCKS startup until that server
connects." That is **wrong in a load-bearing way** and cost a design cycle.
It is a *deadline*, and on expiry Claude Code **proceeds degraded**.

## The actual mechanism

Decompiled from the 2.1.219 binary:

```js
o = !su(process.env.MCP_CONNECTION_NONBLOCKING)  // su() = "explicitly 0/false/no/off"
s = configs.filter(u => u.alwaysLoad === true)   // tier "regular-required"
a = configs.filter(u => u.alwaysLoad !== true)   // tier "regular"
Promise.all([
  Vyl(false, () => zyl(s, "regular-required"), "--mcp-config alwaysLoad servers"),
  Vyl(o,     () => zyl(a, "regular"),          "--mcp-config servers")
])
```

`Vyl(nonBlocking, fn, label)` — first arg true = fire-and-forget. The
alwaysLoad tier passes literal `false`, so it is **always blocking**,
immune to env. But the wait itself is:

```js
let s = GVu();                                 // MCP_CONNECT_TIMEOUT_MS, default 5000
let c = await l.awaitEachWithDeadline(i, a);   // a = budget remaining
if (c > 0) w(`[MCP] ${r}: ${c}/${i.length} not ready after ${s}ms — proceeding; background connection continues`)
```

Key facts:
- Deadline is **per TIER, shared**, not per server. Default 5000ms.
- On expiry it **starts the session anyway**. Not a barrier.
- Tunable via `MCP_CONNECT_TIMEOUT_MS`. Raising it costs nothing when
  servers are fast — `awaitEachWithDeadline` resolves as soon as every
  per-client promise settles (including failures, so a dead server does
  not burn the full budget; `MCP_TIMEOUT`, default 30s, bounds each spawn).
- `MCP_CONNECTION_NONBLOCKING` has **inverted sense**: unset = nonblocking;
  set to `0/false/no/off` = blocking. Do NOT set it blanket — it makes
  startup also wait on the claude.ai connectors, which sit at `needs-auth`
  forever when unattended.

## No hook can gate on this

Measured live (`claude -p --output-format stream-json`, event order):

```
 0-5  system/hook_started   SessionStart:startup
 6-11 system/hook_response  SessionStart:startup
 12   system/init           mcp_servers=[{name,status}, ...]
```

**SessionStart hooks complete BEFORE the init event that reports MCP
status.** A hook cannot observe registration — it runs too early and
out-of-process. Upstream has no post-MCP-connect hook phase:
anthropics/claude-code#26112 (open feature request). Related: #41778 —
scheduled/Remote Trigger sessions miss the deferred tool list entirely.

## No load order exists

Both tiers launch in one `Promise.all`; inside `zyl` every server in a tier
goes to a single `getMcpToolsCommandsAndResources(cb, group)` with per-client
callbacks. Fully concurrent. Key order in `~/.claude.json` means nothing.
A "sentinel MCP server that loads last" is not implementable — there is no
last, and the only priority primitive is the binary alwaysLoad tier split.

## The model CAN enumerate MCP servers in-session

Not via a tool call — by reading its own tool surface. Eager MCP tools appear
in the function list; deferred ones are named in a system-reminder. Both use
`mcp__<server>__<tool>`; split on `__`. Verified: yielded exactly 12 servers,
matching both `claude mcp list` and the init event. This is *in-session
truth*, unlike `claude mcp list` (spawns a second copy of every server, ~5s,
reports a different process's state).

Tested and rejected: `ToolSearch("mcp server tools")` is a ranked keyword
search, not enumeration (returns LSP/Monitor/WebFetch noise).
`ListMcpResourcesTool()` returns "No resources found" — our servers expose
tools, not resources.

Caveats: presence ≠ ready for OAuth connectors (the 3 claude.ai ones are in
the tool surface while `needs-auth`); for stdio servers tools only appear
after a successful handshake, so presence ⇒ ready. Init-event display names
are sanitized in tool prefixes (`claude.ai Gmail` → `claude_ai_Gmail`).

**Enumeration cannot detect its own incompleteness** — an unregistered server
is simply absent, identical to "not configured". Any check must diff against
a hardcoded expected set.

## What ccenv actually does (bundle v0.11.0 / ccmemory v0.13.0)

- `install.sh:enable_always_load()` marks **ccmemory** and **ccteam** only.
  ccprospect/ccinsight left deferred on purpose (install.sh:740,775) — their
  wake-time work is not turn-1 work. ask_* left deferred so their tool defs
  don't eat prompt tokens.
- ccmemory's `SESSION_PROTOCOL` (hooks.py) carries the software fallback:
  call errors → retry 3x; tool **absent** → one
  `ToolSearch("select:mcp__ccmemory__memory_list")`, then STOP and tell the
  user. Rationale: an absent tool raises no error, so the default outcome is
  a session that silently concludes "no prior memory on this" and looks
  clean in the transcript.

## How to set alwaysLoad (all non-obvious)

- No `claude mcp add` flag exists for it.
- `claude mcp get`/`list` do NOT surface it → read `~/.claude.json` directly.
- Set via `claude mcp add-json -s user <name> '<entry-with-alwaysLoad>'`;
  `add-json` refuses to overwrite, so `claude mcp remove -s user <name>`
  first. Prefer this over rewriting `~/.claude.json` in python — claude's
  JSONC editor edits surgically, `json.dump` reformats the whole 256KB file.

## Still unverified

Whether a server that connects AFTER the prompt is built becomes
ToolSearch-able mid-session, or whether the deferred registry is frozen at
turn 1. If frozen, the ToolSearch recovery line in SESSION_PROTOCOL is a
no-op and the honest protocol is "report, let turn 2 pick it up."
