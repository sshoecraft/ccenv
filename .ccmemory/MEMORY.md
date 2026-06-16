## feedback
- [install-claude-md-component-owned](install-claude-md-component-owned.md) — Top-level install.sh owns only the BASE ~/.claude/CLAUDE.md (in a [CCENV MANAGED] marker region); each component owns/appends its own section.
- [no-git-checkout-to-undo-own-edits](no-git-checkout-to-undo-own-edits.md) — NEVER run git for ANY reason without explicit direction — including read-only checks (status/diff/log). The ban has no exceptions.
- [no-per-component-venvs](no-per-component-venvs.md) — ccenv components install via pip3 install --user (pipx as PEP 668 fallback); per-component venvs are explicitly rejected
- [no-version-history-in-claude-md](no-version-history-in-claude-md.md) — Version history goes in CHANGELOG.md, not CLAUDE.md. CLAUDE.md is for purpose/architecture/conventions, never for changelog content.
- [one-line-copy-paste-commands](one-line-copy-paste-commands.md) — Copy-paste shell commands in install output MUST be one physical line — never split with backslash continuations

## project
- [ccenv-installed-vs-source-version](ccenv-installed-vs-source-version.md) — "Installed ccenv version" lives in ~/.config/ccenv/installed-version, NOT /src/ccenv/VERSION. NFS-shared /src makes the source VERSION useless as an…
- [ccenvmcp-stdlib-mcp-shim](ccenvmcp-stdlib-mcp-shim.md) — ccenvmcp: stdlib-only Python 3.9+ FastMCP-compatible shim replacing the mcp SDK across ccmemory/ccusage/ccteam so the bundle installs on 3.9.
- [ccloop-stop-hook-return-0-kills-session](ccloop-stop-hook-return-0-kills-session.md) — ccloop Stop hook MUST block, not return 0, to keep the session alive — ccloop's runner relays on session-end. "No-op" semantics differ from pure Clau…
- [ccmemory-debian-build-unknown-install-layout](ccmemory-debian-build-unknown-install-layout.md) — RESOLVED + VERIFIED on-box (2026-06-13): install.sh ensure_build_toolchain bootstrap fixed the PEP 621 UNKNOWN/install_layout build crash on Debian 3…
- [pythonuserbase-banner-platform-gated](pythonuserbase-banner-platform-gated.md) — install.sh's REQUIRED-setup banner is gated on the platform's DEFAULT user-base; silent on Linux, fires on macOS+Homebrew Python
- [shared-userbase-compiled-dep-abi-mismatch](shared-userbase-compiled-dep-abi-mismatch.md) — PYTHONUSERBASE shares ONE version-agnostic site-packages across pythons; a python bump strands stale-ABI .so files. install.sh v0.1.5 auto-heals via…

## reference
- [ccloop-install-heals-own-hooks](ccloop-install-heals-own-hooks.md) — Top-level install.sh runs `ccloop install` after pip-installing ccloop so it self-heals stale PostToolUse/Stop hook paths
- [mcp-heal-stale-command-pattern](mcp-heal-stale-command-pattern.md) — install.sh's register_mcp() compares stored Command+Args to the desired command and re-registers when stale — never simplify it away
- [pythonuserbase-in-zshenv](pythonuserbase-in-zshenv.md) — PYTHONUSERBASE must be exported in ~/.zshenv (not ~/.zshrc) or Claude hooks/statusLine/MCP fail with ModuleNotFoundError
- [src-tree-appledouble-sidecars](src-tree-appledouble-sidecars.md) — /src tree is on an xattr-less FS that spawns macOS ._* AppleDouble sidecars on every write; corrupts pip wheels — build from clean /tmp stage
