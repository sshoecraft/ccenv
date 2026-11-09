---
name: feedback-do-not-repeat-install-reminders
description: Do not keep telling the user a ccenv change still needs installing; say it at most once when the work is handed over.
metadata:
  type: feedback
---

Do not repeat that a change still needs installing or is not live. The user runs `install.sh` themselves and knows it.

**Why:** The user objected directly after several replies in a row ended with the same reminder: "you don't need to keep fucking reminding me that it needs fucking installed."

**How to apply:** Mention install state at most once, when handing over finished work, and only if it changes what the user should do next. Do not add it to answers about how the code behaves.
