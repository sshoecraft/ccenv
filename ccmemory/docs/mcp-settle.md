# MCP settle stall (`CCMEMORY_MCP_SETTLE_SECONDS`)

An opt-in wall-clock delay in the SessionStart hook, added in ccmemory
v0.14.0 (ccenv bundle v0.14.0). **Off by default.**

## The problem it addresses

The SessionStart protocol orders the model to call `memory_list()` as its
first action. MCP servers connect in the background while the session starts,
so on a slow box the ccmemory tools can be absent from the tool surface on
turn 1. That failure is silent — an unregistered tool raises no error, it is
simply not there, which is indistinguishable from a project that has no
ccmemory. Every subsequent "no prior memory on this" conclusion is wrong and
the transcript looks clean.

ccmemory already has a *software* fallback for this (the retry / conditional
`ToolSearch` / STOP-and-tell-the-user branch in the protocol text). The stall
is a second, mechanical line of defense: hold the hook, and the background
connects get that much longer to land before turn 1.

## Why it is a blind timer

Because nothing better exists. Verified against Claude Code 2.1.219/2.1.220:

- SessionStart hooks complete **before** the `init` event that reports
  `mcp_servers`, so a hook cannot observe connection status
  ([claude-code#26112](https://github.com/anthropics/claude-code/issues/26112)).
- There is no post-MCP-connect hook phase to attach to.
- `alwaysLoad` was never a barrier — a shared 5000 ms deadline that starts the
  session anyway on expiry — and ccenv strips it as of v0.13.2 (see
  `mcp-alwaysload-blocks-startup` in project memory).

So the handler cannot poll for readiness and exit early. It sleeps a fixed
duration and hopes. That is the entire mechanism, and it is why the default
is 0.

## Why it defaults to off

It buys a probability, not a guarantee, and it charges *every* fresh session
on the box — every `claude` invocation, every ccloop relay, every scheduled
run — whether or not that session was ever at risk. A guaranteed cost for a
probabilistic benefit is a trade only the operator can make, on a box where
they have actually watched the tools miss turn 1.

Baseline hook cost with the stall off is ~200 ms.

## Behavior

`hooks._settle_for_mcp(source)`, called from `session_handler` immediately
after the memory-dir gate:

| Condition | Result |
|---|---|
| env unset | no stall (`MCP_SETTLE_SECONDS_DEFAULT = 0.0`) |
| `0` or negative | no stall |
| unparseable (`"soon"`) | no stall — falls back to the default, never raises |
| positive, `source` in `startup`/`resume` | announce, then `time.sleep(n)` |
| positive, `source` in `compact`/`clear` | no stall |

The `compact`/`clear` exclusion matters: those sources reuse the live
process, where the MCP servers connected long ago. Stalling there is pure
latency for zero benefit, and it would hit mid-work rather than at launch.

Projects with no resolvable memory dir return before the stall is even
considered — a non-ccmemory project never pays.

## The announcement

`_announce()` writes the line to stderr **and**, best-effort, to `/dev/tty`.
Hook stderr only surfaces in transcript mode, so on a default TUI a 12 second
freeze with no output reads as a hang. The `/dev/tty` write is wrapped in a
bare `except` — headless contexts (ccloop, cron, Remote Trigger) have no
controlling terminal and fall back to stderr alone.

## Ceiling

Claude Code's default hook timeout is 60 s; a hook that exceeds it is killed,
taking the protocol injection with it. Any configured value must stay well
under that. The stall is not capped in code — the operator sets it, and a
value near the timeout is their call, but 12 s is the tested figure.

## Tests

`tests/test_hooks.py`: `test_session_does_not_settle_by_default`,
`test_session_settles_for_mcp_on_startup_when_opted_in`,
`test_session_does_not_settle_on_compact_even_when_opted_in`,
`test_session_settle_disabled_by_explicit_zero`,
`test_session_settle_env_override`,
`test_session_settle_garbage_env_falls_back_to_default`.

`_record_settle()` stubs both `time.sleep` and `_announce` — the suite must
never actually sleep, and must never scribble on the runner's tty.
