## feedback
- [compiled-ccenv-install-versioning-and-ownership](compiled-ccenv-install-versioning-and-ownership.md) — ccenv install/versioning rules: one version per piece of work at bundle level, installer never writes source, no venvs, component-owned CLAUDE.md, no…
- [compiled-verification-and-restraint-feedback](compiled-verification-and-restraint-feedback.md) — Assorted user corrections: verify our own prose, doc placement, hook gates, installer restraint, no handoff docs, scripts not in /tmp, fixture-only t…

## project
- [compiled-ccenv-environment-and-ccmemory-traps](compiled-ccenv-environment-and-ccmemory-traps.md) — Assorted ccenv lessons: NFS/AppleDouble /src traps, bm25 test fixtures, list budget, ccloop stop, attribution keys, agent rules, quota, ccprospect.
- [compiled-ccenv-version-source-of-truth](compiled-ccenv-version-source-of-truth.md) — ccenv version handling: installed-vs-source marker, importlib.metadata for __version__, top-level bundle bump, CHANGELOG-only history.
- [compiled-ccloop-lifecycle](compiled-ccloop-lifecycle.md) — ccloop session lifecycle: event-driven relay, Stop-hook blocking, two orphan mechanisms, CLI flag threading, handoff-doc reversal, cutoff not a cost…
- [compiled-claude-md-governance](compiled-claude-md-governance.md) — How ~/.claude/CLAUDE.md is assembled/owned by install.sh, why repo CLAUDE.md is its verbatim source, and CLAUDE.md's content boundary (no changelog).
- [compiled-mcp-tool-availability](compiled-mcp-tool-availability.md) — ccmemory MCP tools going missing: install.sh healing stale registrations, alwaysLoad's real behavior, and defensive protocol design
- [compiled-python-userbase-install-pitfalls](compiled-python-userbase-install-pitfalls.md) — ccenv Python --user install traps: shared PYTHONUSERBASE ABI strandings, zshenv export, banner gating, stdlib MCP shim, old setuptools build crash

## reference
- [pythonuserbase-in-zshenv](pythonuserbase-in-zshenv.md) — PYTHONUSERBASE must be exported in ~/.zshenv (not ~/.zshrc) or Claude hooks/statusLine/MCP fail with ModuleNotFoundError
