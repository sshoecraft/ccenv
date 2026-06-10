---
name: one-line-copy-paste-commands
description: Copy-paste shell commands in install output MUST be one physical line — never split with backslash continuations
metadata:
  type: feedback
tags: [install, ux, shell]
---

When printing copy-paste commands in install.sh output (or any user-facing instruction), they MUST be a single physical line. Do NOT split with `\` continuations across lines, even for readability. The user said: "THIS _MUST_ be 1 line!!! MUST".

Example, what NOT to do:
    grep -q PYTHONUSERBASE $HOME/.zshrc 2>/dev/null || \
      echo 'export PYTHONUSERBASE="$HOME/.local"' >> $HOME/.zshrc

What TO do:
    grep -q PYTHONUSERBASE $HOME/.zshrc 2>/dev/null || echo 'export PYTHONUSERBASE="$HOME/.local"' >> $HOME/.zshrc

**Why:** Users copy-paste these into their terminal. Backslash continuations break under any of: terminal wrap behavior, partial selection, paste through certain SSH clients, or being read aloud. A single physical line works in every case. The user enforces this strictly.

**How to apply:** In install.sh's REQUIRED-setup banner and any similar output, write commands as one `echo` per command — no `\` line splits, no multi-line heredocs the user is expected to retype.
