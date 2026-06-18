---
name: ccloop-relay-not-cutoff-dependent
description: ccloop's context-full relay is event-driven (the wall event), NOT the token cutoff; cutoff is only an early-relay knob and must never be the sole gua…
metadata:
  type: project
---

ccloop's whole purpose is: when context fills, summarize + restart in a fresh session. The token `cutoff` (`<run-dir>/cutoff`, default 250k) is an ABSOLUTE token count with no relation to the model's real context window. It is **only an *early* relay knob** — it must never be the only thing keeping a run off the hard wall.

**Why:** A real run wedged on the 200K context wall with `cutoff=500000` (set 2.5× the window deliberately, as a test). `tokens >= cutoff` could never trip below the wall, so no relay ever fired. Compounded by the old shared `/tmp/ccusage-<uid>.json` cache being clobbered by a concurrent same-UID session (foreign `session_id` → `exact_tokens` returned None → gate fail-open). Either alone guarantees the wedge.

**How to apply:** The hard guarantee is the deterministic wall event (see [[context-wall-deterministic-signal]]), reacted to in `runner` + `transcript.hit_context_wall` — independent of cutoff and cache. Do NOT "fix" this by adding another magic threshold (e.g. "relay at 85% of window") — the user explicitly rejected replacing one hand-set number with another. React to the real event.

Cache redesign (ccusage v0.3.0): per-session file `$XDG_STATE_HOME/ccusage/<session-id>.json` (default `~/.local/state/ccusage/`), pruned after 2 days. Kills the concurrent-clobber fail-open. ccloop reads its own session file (legacy `/tmp` honored as transition fallback); MCP server reads newest file. Shipped in ccenv v0.2.0 (ccloop 0.6.0 + ccusage 0.3.0).
