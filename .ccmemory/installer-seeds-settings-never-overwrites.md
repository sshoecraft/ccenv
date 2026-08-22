---
name: installer-seeds-settings-never-overwrites
description: install.sh settings step SEEDS keys — a key already present in ~/.claude/settings.json is the user's choice and is never overwritten, only reported.
metadata:
  type: feedback
tags: [install.sh, settings.json, ccenv]
---

# Installer-written settings are seeded, not owned

ccenv v0.22.0 added `install_ccenv_settings()` to `install.sh` (step name
`settings`, runs right after the base CLAUDE.md is assembled). It writes two
keys into `~/.claude/settings.json`:

- `env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK = "1"` — kills the refusal
  fallback, which silently re-runs a turn on a different model.
- `switchModelsOnFlag = false` — belt and braces if the env var fails to
  propagate; costs a dialog instead of a silent swap.

## The correction

First cut *enforced* both values ("ccenv owns these two") and rewrote a key
that held a different value. The user cut in mid-implementation: **"now make
sure you dont set it if its already there."**

The rule: an installer seeds a *missing* key. A key that is already present is
a deliberate user choice — leave it, whatever the value, and print
`already set, left alone: <key>=<value>`. Do not "correct" it. Also: when
nothing was added, do not rewrite the file at all (the mtime is a signal).

Generalizes past this one step — any ccenv install path touching a user's
config file (`settings.json`, shell env files, `.claude.json`) is
add-if-missing, never enforce-my-value. The exception is wiring ccenv itself
owns end to end (its own hook entries, its own MCP registrations), where
healing a stale path IS the point.

## Tests

`tests/test_settings_step.sh` — lifts the function out of `install.sh` by name
(`sed -n '/^install_ccenv_settings() {$/,/^}$/p'` + `eval`) and runs it against
throwaway /tmp HOME fixtures, so the real code is tested without running the
installer. 19 assertions, incl. present-value-kept and file-not-rewritten.
