---
name: compiled-mcp-tool-availability
description: ccmemory MCP tools going missing: install.sh healing stale registrations, alwaysLoad's real behavior, and defensive protocol design
metadata:
  type: project
tags: [compiled, mcp, ccmemory, install, tool-surface]
---

## Topic

Three incidents, same root class of failure: a Claude Code session cannot see
or use ccmemory's (or another component's) MCP tools, for reasons that span
install-time registration, harness loading semantics, and injected-protocol
text that assumes a tool is there. Chronological.

## 1. install.sh must heal stale registrations, not skip-if-present

Two components independently hit the same bug shape: a binary's registered
path goes stale (user moves from a manual venv to `~/.local/bin`, or from a
pre-PYTHONUSERBASE layout), the registration entry still "exists" by name, and
a skip-if-registered installer walks past it forever. The user was stuck
manually running `claude mcp remove` / re-registering by hand.

### ccloop hooks
- `ccloop` owns its own pair of hooks: `PostToolUse -> ccloop guard` and
  `Stop -> ccloop keepgoing`. Auto-registered on first run of a ccloop task,
  storing the absolute path to the binary at that time. When the binary moves
  (e.g. manual venv `~/.venvs/ccloop/bin/ccloop` -> `~/.local/bin/ccloop`) the
  registrations stay pinned to the OLD path and every Stop event fires
  `ModuleNotFoundError: No module named 'ccloop'` from the dead path.
- Fix: top-level `install.sh` runs `ccloop install` immediately after
  `pip3 install --user ccloop`. That subcommand calls
  `install.ensure_registered()`, which uses a deliberately LOOSE matcher
  (`_is_ours`: command basename is or contains `ccloop`) to detect and rewrite
  any stale entry — legacy venv paths, the bash-era hook, whatever
  accumulated. User does NOT have to run `ccloop install` by hand.
- Code: `ccloop/src/ccloop/install.py:_is_ours()` and `ensure_registered()`.
- Why: ccloop is the only ccenv component whose hooks are not owned by
  `register_mcp()` or the top-level CLAUDE.md assembly; it manages its own
  settings.json entries. Without the post-install call, anyone who ever did a
  manual venv install sees broken hook errors after every Stop event, even
  after a clean re-run of the top-level installer.
- Apply: if the ccloop hook installer is extracted/refactored, preserve
  `_is_ours`'s loose matching — a strict equality match (heal only if new path
  = old path) defeats the point. Keep the `ccloop install` invocation in
  install.sh; it is deliberately not a one-time fix.
  `ccloop-install-heals-own-hooks`

### MCP registration
- `install.sh`'s `register_mcp()` does more than check-and-skip:
  1. `mcp_registered "$name"` checks existence;
  2. if present, `mcp_current_command "$name"` parses `claude mcp get` output,
     gluing the `Command:` and `Args:` lines into one string;
  3. compares to `$*` (what would be registered now);
  4. if different: prints "registered with stale command — re-registering",
     runs `claude mcp remove`, then `claude mcp add` with the new command.
- Failure it prevents: ccmemory/ccteam pinned to a bare `ccmemory` binary name
  from a pre-PYTHONUSERBASE install, or `~/Library/Python/3.14/bin/ccmemory
  mcp` (macOS) after switching to `~/.local`. `claude mcp list` shows
  `✘ Failed to connect`, and re-running install.sh did nothing because
  `mcp_registered` returned true. User had to manually `claude mcp remove
  ccmemory` and re-run; this dragged on an entire session before being
  identified as the root cause of ccmemory/ccteam connection failures on
  macOS.
- Same compare-and-heal lives in Python at
  `ccusage/install.py:register_mcp_user()`. Any new component's installer must
  go through `register_mcp()` or replicate the heal — never inline a bare
  `claude mcp add`, never bring back "leaving as-is". Never simplify the
  pattern away. `mcp-heal-stale-command-pattern`

Generalized lesson: a registration check that only asks "is it present" is not
a health check. It must ask "is it present AND CURRENT," or staleness is
permanent and self-inflicted.

## 2. `alwaysLoad` is a bounded deadline, not a load guarantee — and it can erase the tool surface

`install.sh` set `alwaysLoad: true` on ccmemory from v0.6.0. **Removed in ccenv
v0.13.2**; `strip_always_load()` now deletes the field instead of setting it.
Two corrections stacked: the first (2026-07-24) was recorded and NOT acted on;
the second (2026-07-25) forced the removal.

### Correction 1 — deadline, not barrier
Decompiled from CLI binary 2.1.219:

```js
o = !su(process.env.MCP_CONNECTION_NONBLOCKING)
s = configs.filter(u => u.alwaysLoad === true)   // tier "regular-required"
a = configs.filter(u => u.alwaysLoad !== true)   // tier "regular"
Promise.all([ Vyl(false, () => zyl(s, "regular-required"), ...),
              Vyl(o,     () => zyl(a, "regular"), ...) ])
```

`Vyl(nonBlocking, ...)`: the alwaysLoad tier passes literal `false`, so it is
always blocking, immune to env. But bounded:

```js
let s = GVu();                                 // MCP_CONNECT_TIMEOUT_MS, default 5000
let c = await l.awaitEachWithDeadline(i, a);
if (c > 0) w(`[MCP] ${r}: ${c}/${i.length} not ready after ${s}ms — proceeding; background connection continues`)
```

- Deadline is per TIER, SHARED, default 5000ms.
- On expiry the session **starts anyway**. So the flag NEVER guaranteed the
  tools were registered — its sole reason to exist.

### Correction 2 — on non-Anthropic models it ERASES the tool surface
Measured on atrader@clyde: Claude Code driving `google/gemma-4-26B-A4B-it`
through an OpenAI-compatible proxy (identifiable by `chatcmpl-` tool_use_ids).
172 transcripts, 2026-06-29 -> 2026-07-25.

| server | alwaysLoad | outcome |
|---|---|---|
| broker / journal / scheduler / searxng | unset | 353-495 successful calls each |
| ccusage | unset | used in 16 sessions, 0 rejections |
| **ccmemory** | **true** | **0 successful memory_list, EVER**; 122 transcripts with `No such tool available` |

- ccusage is the control: same bundle, installer, box, proxy, session; only
  the flag differs. It worked.
- Not a deferral: no deferred-tool system-reminder ever named the ccmemory
  tools; they were absent from the tool surface outright.
- Server was healthy: `claude mcp list` Connected, MCP handshake 0.18s,
  SessionStart hook fired normally in all 5 newest transcripts. That is why
  the model knew the name `mcp__ccmemory__memory_list` (from injected protocol
  prose) and called a tool it had never been offered — it was not "detecting
  an absent tool."

### Still true
- **No hook can gate on MCP status.** SessionStart hooks complete BEFORE the
  init event that reports `mcp_servers`. Upstream has no post-MCP-connect hook
  phase (anthropics/claude-code#26112); scheduled/Remote Trigger sessions miss
  the deferred tool list entirely (#41778).
- **No load order exists.** Both tiers launch in one concurrent `Promise.all`.
  Key order in `~/.claude.json` means nothing; a "sentinel server that loads
  last" is not implementable.
- **The model CAN enumerate its own MCP surface** by reading its tool list —
  eager tools appear as functions, deferred ones are named in a
  system-reminder; split `mcp__<server>__<tool>` on `__`. This is in-session
  truth. `claude mcp list` is NOT: it spawns a second copy of every server and
  reports a different process's state (why it showed ccmemory Connected on a
  box where the tools were never available).
- **Enumeration cannot detect its own incompleteness** — an unregistered
  server looks identical to "not configured". Any check must diff against a
  hardcoded expected set.
- Setting/removing the field: no `claude mcp add` flag exists; `claude mcp
  get`/`list` do not surface it; read `~/.claude.json` directly and rewrite via
  `claude mcp remove -s user <name>` + `claude mcp add-json -s user <name>
  '<entry>'` (add-json refuses to overwrite). Prefer this over rewriting the
  256KB file with `json.dump`.

### The lesson
A note saying "this mechanism does not do what its name says" is a finding to
ACT on immediately, not a footnote. Correction 1 sat unused for a day while the
predicted symptom was live on a production box, and the diagnosis was
re-derived from scratch — badly, twice — before anyone re-read it.
`mcp-alwaysload-blocks-startup`

## 3. Injected protocol text must not assume its own remedy tool exists

(ccmemory v0.13.0 -> v0.13.1.) `SESSION_PROTOCOL` is injected into every
session by the ccmemory SessionStart hook. v0.13.0 added handling for
"`memory_list` is not in your tool list at all," as a bare imperative:

> Call `ToolSearch("select:mcp__ccmemory__memory_list")` once to try to pull
> in a late-connecting server.

This is the successor to item 2, attacked at the protocol layer. `ToolSearch`
only exists when the harness has tool-search enabled; without it, a model that
correctly detected the missing tool dead-ended hunting for the tool with which
to find a tool. Observed on a gemma-4-26B session: it searched its tool list
for `ToolSearch`, didn't find it, re-read the list, then burned a turn on a
`Bash` call whose body was its own reasoning pasted in as `#` comments.

Narrower regression, not a wash: v0.13.0's target was SILENT failure — a
session with no ccmemory reads exactly like a project with no memory, so the
model confidently concludes "no prior memory exists." That hole closed;
"silently wrong" became "visibly stuck": better, not right. Do not let the
second failure discredit the first fix, nor the first fix excuse the second.

Fixed in v0.13.1. Four rules for any injected "call tool X":
1. Gate it — "IF, and only if, X is in your tool list."
2. State X's absence is NORMAL and say to SKIP rather than hunt for it; a
   model told to call a missing tool will look for it.
3. Forbid the shell fallback by name — nothing run in bash can register an MCP
   tool, but models WILL try.
4. Make the terminal/stop state reachable from EVERY branch. v0.13.0's "If the
   tool is still unavailable after that, STOP" hung off the ToolSearch branch
   only, so the no-ToolSearch path had no exit.

Surfaced on gemma-4-26B, not Opus: protocol text is executed by whatever model
the user points at the box, including small ones driving `instenv.prompt`
fan-outs. Write injected text for the least capable model that will ever read
it. `prescribed-remedy-must-not-assume-its-own-tool-exists`

## Cross-cutting takeaways

- Registration and loading are different failure layers (installer vs.
  harness vs. injected protocol); each independently produced a "tools
  silently unavailable" incident. Fixing one does not cover the others.
- Anything that checks "is the MCP tool there" — health check, installer, or
  protocol text read by the model — must treat absence as an expected, handled
  case with a defined exit, never an assumed-present precondition.
- Weak/non-Anthropic models are the real test case: they drive unattended
  fan-outs, and every one of these failures was first measured there, not
  where first suspected.
