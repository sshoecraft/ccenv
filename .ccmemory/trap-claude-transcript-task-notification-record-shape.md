---
name: trap-claude-transcript-task-notification-record-shape
description: Claude Code 2.1.322 logs a delivered background-task notification as type=attachment (commandMode task-notification), not type=user.
metadata:
  type: project
---

Anything that reads a session transcript (.jsonl) to learn whether a backgrounded task's answer reached the model must accept more than one record shape.

- `queue-operation` (operation enqueue/…) carries the `<task-notification>` text but only means queued — not seen by the model.
- Older Claude Code: delivery was a `{"type":"user","message":{"content":...}}` record.
- 2.1.322: delivery is `{"type":"attachment","attachment":{"type":"queued_command","commandMode":"task-notification","prompt":"<task-notification>…","origin":{"kind":"task-notification"}},"rendered":[…],"renderedRole":"system"}`.

What bit us: ccdirector's `progress.task_status` matched only `type == "user"`, so a director's backgrounded ask_astra answer (delivered within 4 minutes) was never recognized; `plan submit` refused for the full 30-minute CONSULT_PATIENCE_SECONDS, then the consult was dropped uncounted. Fixed in ccdirector 0.13.1 via `delivered_text()`.

How to apply: when a transcript-parsing check silently never matches, grep the real transcript for the identifier and classify each matching line's top-level `type` before assuming the event did not happen. Claude Code transcript shapes change without notice.
