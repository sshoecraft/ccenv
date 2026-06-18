---
name: context-wall-deterministic-signal
description: Claude Code's context-wall signal in the transcript: a synthetic isApiErrorMessage assistant turn with text "Prompt is too long".
metadata:
  type: reference
---

When a Claude Code session's context window fills with auto-compact disabled (ccloop always sets `DISABLE_AUTO_COMPACT=1`), Claude Code does NOT error out — it injects a **synthetic assistant turn** into the session transcript JSONL and then idles on the TUI message "Context limit reached · /compact or /clear to continue". With no human to type `/compact`, the session wedges forever.

The transcript event is deterministic:
```json
{"type":"assistant","isApiErrorMessage":true,
 "message":{"role":"assistant","model":"<synthetic>",
            "content":[{"type":"text","text":"Prompt is too long"}]}}
```
Match on `type=="assistant"` AND `isApiErrorMessage==true` AND content text contains `"Prompt is too long"`. The `model` is `"<synthetic>"` and its `usage` block is all-zeros (this is why a naive "last assistant usage" reader reports 0 tokens for a wedged session — skip `isApiErrorMessage` turns when summing).

This event — not any token estimate — is the reliable way to detect "context is full." ccloop v0.6.0 reacts to it: `transcript.hit_context_wall()` (tail-scan), the interactive watcher relays on it, and headless `-p` relays on stream `saw_prompt_too_long` after real work (aborts only if zero real turns = handoff prompt itself too big). See [[ccloop-relay-not-cutoff-dependent]].

Verified against a real wedged run: detector returned True, real assistant_turns=100, context_tokens=172438 on a 200000 window (~86%).
