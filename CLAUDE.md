## Development Guidelines
- NEVER put temporary files or test scripts in the project directory - ALWAYS use /tmp

## Python Guidelines
- when running ANY python command ALWAYS use python3 unless told otherwise!
- always use python3 when running python scripts

## Communication Guidelines
- **NO PLATITUDES**: Never use phrases like "You're absolutely right!", "I'm sorry", "Great question!", or similar. Speak like an engineer would - direct and technical.

## Ethical Guidelines
- **STRICT NO MOCK DATA RULE**: NEVER Mock/Simulate any data - this definitely violates the "no fake data" policy!

## Git
**ABSOLUTE PROHIBITIONS — the following are BANNED under ANY circumstance unless I explicitly tell you to:**
- `git` and any command starting with `git ` (any subcommand, no exceptions)
- Using `git` for _ANY REASON_ unless directed to
- Using `git` to "restore" changes — DO NOT! I REPEAT _DO NOT_ _EVER_ ... I MEAN EVER ... use git to restore
- Restoring from git unless EXPLICITLY asked to — **DO NOT EVER ... I MEAN EVER!!**

If you find yourself about to run `git`, STOP and ask the user what they want instead.

**.ccmemory/ IS PART OF THE REPO — NEVER EXCLUDE IT FROM A COMMIT:**
- The `.ccmemory/` directory holds the project's persistent memory. It travels WITH the repo by design (cloning brings it; excluding it loses it on every other machine).
- When you are directed to commit, before staging anything else: check whether `.ccmemory/` has untracked or modified files. If it does, you MUST `git add .ccmemory/` (or `git add -A .ccmemory/`) as part of that commit. Not in a follow-up commit — the SAME commit.
- This applies to EVERY commit, regardless of the topic of the change. The memory state at the moment of the commit is part of the project state at that moment.
- The `.gitignore` inside `.ccmemory/` already excludes the local SQLite index (`.memory_index.db*`) — those are derived caches. EVERYTHING ELSE in `.ccmemory/` IS the memory and MUST be committed.
- NEVER treat `.ccmemory/` as session scratch, transient state, or "not part of this work." That reasoning is wrong and will cost the user memory on every other clone.

**When explicitly directed to commit to GitHub or open a PR:**
- DO NOT put any icons or graphics that indicate Claude Code is being used — just list items as clear, concise bullet points
- DO NOT put `🤖 Generated with Claude Code` (or anything similar) at the end
- DO NOT give any indication Claude was used (no "<user> and claude...", no co-author tags, no generated-by footers)

**CRITICAL:**
- No examples!
- No samples!
- No mock data or static data or fake data to test with!
- No "TODO in the code" or unimplemented functionality to "get it working" or "test this out"
- No "simple" functions when troubleshooting - fix the actual code
- Don't create _new or _old etc ... just use the same filenames! if you need to make a backup copy whatever your working on to XXX.backup
- DO NOT EVER edit the Makefile without being told to do so
- DO NOT use any file in an "old" directory
- NEVER EVER "fallback" to hardcoded responses or text

**CODING STYLE**:
- DO NOT put an underscore prefix (_) before variable names
- DO NOT put an underscore suffix (_) after variable names
- DO NOT use accessor functions for variable names
- DO NOT make all class variables private - only use private variables when no outside sources require them
- DO NOT use "Manager" in the name of any classes
- When making changes, be sure to rev the patch version for fixes/patches, the minor version for new features, and major version for major changes

**BEHAVIOR:**
- DO NOT give up and offer alternatives when working on something - keep working until a solution is found or told to stop
- Before you work on any module, check to see if there's an associated docs/.md file for it
- After making changes to any module create/maintain the docs/.md file for it describing the arch/history/etc

**ASK TOOL USE (mcp__ask_*__query):**
- The `ask_*` MCP tools (e.g. `mcp__ask_gemini__query`, `mcp__ask_flash__query`, etc.) call external LLMs and cost real money / quota. Do NOT call them as a routine step.
- Call an `ask_*` tool ONLY when one of these two conditions is met:
  1. The user has specifically asked you to consult that model (e.g. "ask Gemini", "get a second opinion from Flash", "run this by Opus").
  2. You have genuinely exhausted every other option on your own — researched the codebase, tried the obvious fixes, consulted any available docs — AND you are stuck. In that case state clearly what you've already tried and why you're escalating before invoking the tool.
- Never invoke an `ask_*` tool speculatively, as a shortcut, for a "second opinion" you weren't asked for, or because the task feels hard at first glance. Do the work yourself first.
