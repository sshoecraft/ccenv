---
name: no-git-checkout-to-undo-own-edits
description: NEVER run git for ANY reason without explicit direction — including read-only checks (status/diff/log). The ban has no exceptions.
metadata:
  type: feedback
tags: [git, rules, violation]
---

The CLAUDE.md ban on `git` is absolute and covers every subcommand, including read-only ones (`git status`, `git diff`, `git log`, `git reflog`). "If you find yourself about to run `git`, STOP and ask the user what they want instead."

Two distinct violations to avoid:

**1. Using git to undo your own edits.** When an edit needs to be undone, NEVER reach for `git checkout -- <file>` / `git restore`. Use Edit/Write to overwrite the file back to the desired state. If you don't have the prior content in your context, ASK the user. There is no exception for "I'm only reverting my own code." The working tree is shared state — `git checkout` could just as easily wipe the user's uncommitted work alongside yours, and "it only affected my changes this time" is luck, not safety.

**2. Using git for "read-only verification."** Even `git status` is prohibited absent explicit direction. Rationalizing "but it's just inspection" is wrong — the rule's text says "any subcommand, no exceptions." When you need to verify file state, use Read / find / wc / grep — never git. If you genuinely need to know what's uncommitted, ASK the user.

**Why:** The user enforces this strictly because git is too blunt and side-effecting a tool for the model to wield unsupervised; even ostensibly-read-only invocations like `git diff` can interact with index state, pagers, hooks, lockfiles. The bright-line rule ("never, unless explicitly told") is what makes the prohibition safe to rely on — any carve-out invites the next ambiguous case.

**How to apply:** Treat EVERY `git ...` invocation as requiring explicit permission. Implicit permissions exist (e.g. "commit and push" allows the commit pipeline; "what's the git status" allows `git status`), but they must come from the user's words, not Claude's interpretation. When in doubt, ask first, run never.
