# [AWARENESS PROTOCOL]

## Project Awareness System

All projects should use the three-layer awareness architecture.

### On session start:
1. Check if this project has an [AWARENESS] section in its CLAUDE.md.
2. If YES: follow the routing table and session protocol from the project-awareness skill.
3. If NO: mention to the user that awareness infrastructure hasn't been set up for this
   project and offer to bootstrap it.

### While working:
- When you discover something important about the codebase that isn't documented
  (an invariant, a pitfall, a cross-subsystem dependency), NOTE IT. At session end
  or before context handoff, update the appropriate awareness doc.
- When you modify a public API (add/remove/rename a function, change a signature),
  update the subsystem doc.
- Prefer reading awareness docs over re-reading source files for information you've
  already captured.

### For Agent Teams:
- Team lead loads: CLAUDE.md + structural-map.md
- Workers load: CLAUDE.md + their assigned subsystem doc
- Workers update their subsystem doc before reporting completion
- Only the team lead updates CLAUDE.md

### Awareness templates location:
Templates are in `~/.claude/skills/project-awareness/templates/`.
When bootstrapping a project, copy relevant templates to the project's
`.claude/awareness/templates/` directory for local reference.
