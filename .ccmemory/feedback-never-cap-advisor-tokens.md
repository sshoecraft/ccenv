---
name: feedback-never-cap-advisor-tokens
description: Never pass a small max_tokens to ask_astra/ask_gpt/ask_fable; a capped call burns its budget on reasoning and returns nothing, paid for twice.
metadata:
  type: feedback
---

When calling an advisor (`mcp__ask_astra__query`, `ask_gpt`, `ask_fable`), leave `max_tokens` unset, or set it very high. Do not cap it to "keep the answer short" — ask for brevity in the prompt instead.

**Why:** A call with `max_tokens=6000` came back `empty content (finish_reason=length)`: `thoughts_tokens` 6000 of 6000, zero answer. The reasoning counts against the cap. The user paid for the failed call and the retry: "Why are you wasting my goddamn money??? give it as much as it needs."

**How to apply:** No `max_tokens` on advisor calls. If a result shows `finish_reason=length`, the cap was the cause; resend uncapped, once.
