## feedback
- [no-per-component-venvs](no-per-component-venvs.md) — ccenv components install via pip3 install --user (pipx as PEP 668 fallback); per-component venvs are explicitly rejected
- [one-line-copy-paste-commands](one-line-copy-paste-commands.md) — Copy-paste shell commands in install output MUST be one physical line — never split with backslash continuations

## project
- [pythonuserbase-banner-platform-gated](pythonuserbase-banner-platform-gated.md) — install.sh's REQUIRED-setup banner is gated on the platform's DEFAULT user-base; silent on Linux, fires on macOS+Homebrew Python

## reference
- [ccloop-install-heals-own-hooks](ccloop-install-heals-own-hooks.md) — Top-level install.sh runs `ccloop install` after pip-installing ccloop so it self-heals stale PostToolUse/Stop hook paths
- [mcp-heal-stale-command-pattern](mcp-heal-stale-command-pattern.md) — install.sh's register_mcp() compares stored Command+Args to the desired command and re-registers when stale — never simplify it away
- [pythonuserbase-in-zshenv](pythonuserbase-in-zshenv.md) — PYTHONUSERBASE must be exported in ~/.zshenv (not ~/.zshrc) or Claude hooks/statusLine/MCP fail with ModuleNotFoundError
- [src-tree-appledouble-sidecars](src-tree-appledouble-sidecars.md) — /src tree is on an xattr-less FS that spawns macOS ._* AppleDouble sidecars on every write; corrupts pip wheels — build from clean /tmp stage
