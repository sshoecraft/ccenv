---
name: mcp-alwaysload-blocks-startup
description: alwaysLoad is REMOVED from ccenv as of v0.13.2. It was a 5s deadline that proceeds degraded, and on non-Anthropic models it erased ccmemory's tool su…
metadata:
  type: reference
---

# MCP alwaysLoad — what it did, and why ccenv removed it

**REMOVED in ccenv v0.13.2.** `install.sh` set `alwaysLoad: true` on ccmemory
from v0.6.0. `strip_always_load()` now deletes the field instead.

Two corrections stacked here. The first (2026-07-24) was recorded and NOT acted
on; the second (2026-07-25) is what forced the removal.

## Correction 1 — it is a deadline, not a barrier

Decompiled from binary 2.1.219:

```js
o = !su(process.env.MCP_CONNECTION_NONBLOCKING)
s = configs.filter(u => u.alwaysLoad === true)   // tier "regular-required"
a = configs.filter(u => u.alwaysLoad !== true)   // tier "regular"
Promise.all([ Vyl(false, () => zyl(s, "regular-required"), ...),
              Vyl(o,     () => zyl(a, "regular"), ...) ])
```

`Vyl(nonBlocking, ...)` — the alwaysLoad tier passes literal `false`, so it is
always blocking, immune to env. But the wait is bounded:

```js
let s = GVu();                                 // MCP_CONNECT_TIMEOUT_MS, default 5000
let c = await l.awaitEachWithDeadline(i, a);
if (c > 0) w(`[MCP] ${r}: ${c}/${i.length} not ready after ${s}ms — proceeding; background connection continues`)
```

- Deadline is per TIER, SHARED. Default 5000ms.
- On expiry it **starts the session anyway**.
- So it NEVER guaranteed the tools were registered — the sole reason to set it.

## Correction 2 — on non-Anthropic models it ERASES the tool surface

Measured on atrader@clyde: Claude Code driving `google/gemma-4-26B-A4B-it`
through an OpenAI-compatible proxy (identifiable by `chatcmpl-` tool_use_ids).
172 transcripts, 2026-06-29 -> 2026-07-25.

| server | alwaysLoad | outcome |
|---|---|---|
| broker / journal / scheduler / searxng | unset | 353-495 successful calls each |
| ccusage | unset | used in 16 sessions, 0 rejections |
| **ccmemory** | **true** | **0 successful memory_list, EVER**; 122 transcripts with `No such tool available` |

**ccusage is the control**: same bundle, same installer, same box, same proxy,
same session. Only the flag differs. It worked.

Not a deferral: no deferred-tool system-reminder ever named the ccmemory tools.
They were absent from the tool surface outright. The server was healthy —
`claude mcp list` ✔ Connected, MCP handshake 0.18s, SessionStart hook fired
normally in all 5 newest transcripts. That last part is why the model knew the
name `mcp__ccmemory__memory_list` (from the injected protocol prose) and called
a tool it had never been offered — it was not "detecting an absent tool."

## Still true and still useful

- **No hook can gate on MCP status.** SessionStart hooks complete BEFORE the
  init event that reports `mcp_servers`. Upstream has no post-MCP-connect hook
  phase (anthropics/claude-code#26112); scheduled/Remote Trigger sessions miss
  the deferred tool list entirely (#41778).
- **No load order exists.** Both tiers launch in one `Promise.all`, fully
  concurrent. Key order in `~/.claude.json` means nothing; a "sentinel server
  that loads last" is not implementable.
- **The model CAN enumerate its own MCP surface** by reading its tool list —
  eager tools appear as functions, deferred ones are named in a
  system-reminder; split `mcp__<server>__<tool>` on `__`. This is in-session
  truth. `claude mcp list` is NOT — it spawns a second copy of every server and
  reports a different process's state (which is exactly why it showed ccmemory
  ✔ Connected on a box where the tools were never available).
- **Enumeration cannot detect its own incompleteness** — an unregistered server
  is indistinguishable from "not configured". Any check must diff against a
  hardcoded expected set.
- Setting/removing the field: no `claude mcp add` flag exists; `claude mcp
  get`/`list` do not surface it; read `~/.claude.json` directly and rewrite via
  `claude mcp remove -s user <name>` + `claude mcp add-json -s user <name>
  '<entry>'` (add-json refuses to overwrite). Prefer this over rewriting the
  256KB file with `json.dump`.

## The lesson

A memory that records "this mechanism does not do what its name says" is a
finding to ACT on, not a footnote. Correction 1 sat unused for a day while the
symptom it predicted was live on a production box, and the diagnosis was
re-derived from scratch — badly, twice — before anyone re-read it.
