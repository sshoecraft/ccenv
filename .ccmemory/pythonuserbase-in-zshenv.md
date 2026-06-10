---
name: pythonuserbase-in-zshenv
description: PYTHONUSERBASE must be exported in ~/.zshenv (not ~/.zshrc) or Claude hooks/statusLine/MCP fail with ModuleNotFoundError
metadata:
  type: reference
tags: [pythonuserbase, zshenv, environment, statusline, hooks, mcp]
---

ccenv installs Python `--user` packages under `~/.local` (install.sh forces `PYTHONUSERBASE=$HOME/.local`). At runtime Python only finds those packages if `PYTHONUSERBASE` is in the **environment** — otherwise user-site falls back to the macOS default (`~/Library/Python/<ver>/...`), which is empty, and every ccenv binary (ccmemory, ccloop, ccusage-statusline) dies with `ModuleNotFoundError`.

The trap: `~/.zshrc` is sourced **only by interactive shells**. Claude Code runs hooks, the statusLine command, and MCP servers in **non-interactive** shells, which do NOT source `~/.zshrc`. So putting the export there makes it work in your terminal but silently fail for everything Claude spawns (blank statusline, broken ccloop/ccmemory hooks).

Fix: export it in **`~/.zshenv`**, which zsh sources on *every* invocation (interactive or not, login or not). Verified: `env -i HOME=$HOME /bin/zsh -c 'echo $PYTHONUSERBASE'` resolves it.

```
# ~/.zshenv
export PYTHONUSERBASE="$HOME/.local"
```

Do NOT add it as `env` in `~/.claude/settings.json` — that's redundant (zshenv already covers every Claude subprocess), non-portable (hardcoded abs path), and install.sh doesn't write it so it won't exist on other machines. install.sh's "REQUIRED: shell environment setup" warning checks the live env var (not the rc file), and now correctly points at `~/.zshenv`.

Related: [[src-tree-appledouble-sidecars]]</body>
</invoke>
