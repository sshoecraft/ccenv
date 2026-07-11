# ccenv changelog

Per the global rule: patch = fix, minor = feature, major = breaking.

## v0.6.0

install.sh: mark the ccmemory and ccteam MCP servers `alwaysLoad: true` at
registration so Claude Code blocks session startup until they connect, instead
of letting the model's first turn begin while they are still connecting in the
background.

Claude Code loads MCP servers **non-blocking by default**: with tool-search on
(the default) each server's tools are deferred behind `ToolSearch` and the
server connects in the background, so the first turn can start before
ccmemory/ccteam register. In a ccloop TUI session that meant the required first
actions — ccmemory's `memory_list()` and ccteam's claim-before-edit — silently
ran without their tools. `alwaysLoad: true` forces those two servers to load
eagerly and gates startup on their connection (~5s/server cap). It fixes the
race in both the TUI and headless, with no ccloop code and no reliance on the
model obeying a prompt instruction — it is a claude-native startup gate.

There is no `claude mcp add` flag for this (alwaysLoad is a field on the
server's JSON entry), so the new `enable_always_load()` helper re-registers each
server through `claude mcp add-json`, carrying its existing command/args/env
untouched (`add-json` refuses to overwrite, so it does `remove` + `add-json`, the
same heal pattern `register_mcp` uses). It runs after `register_mcp` so a
heal-triggered re-register re-applies the flag, is idempotent, and only touches
ccmemory/ccteam — `ask_*` stay deferred so their tool schemas don't cost prompt
tokens on every turn. Existing installs pick it up on the next `install.sh` run.

## v0.5.0

ccloop v0.9.0: `--model=NAME` flag. The model for a run's spawned claude
sessions could previously only be set via the `CCLOOP_MODEL` env var; the
natural `ccloop --model=opus ...` invocation failed with `unknown option`.
The flag takes an alias (`opus`, `sonnet`, `haiku`) or a full model id,
accepts both `--model=NAME` and `--model NAME` forms, works with
`--resume-run` too, and wins over `CCLOOP_MODEL` when both are set. Like the
env var, it applies to the ccloop invocation at hand — a resume does not
remember the model the run was started with.

Internals: `--cutoff` and `--model` now share one generic value-flag
extractor in `cli.py`; the model threads `cli → cmd_run/cmd_resume → loop`
as an explicit parameter (no env mutation). The `fake_claude` test shim
gained `FAKE_ARGS_FILE`, which records each invocation's argv so tests can
assert what actually reaches the claude command line.

## v0.4.1

install.sh: handle PEP 668 "externally-managed-environment" (Debian 12+, Ubuntu
23.04+, Fedora 38+, Arch, Homebrew Python). Those distros drop an
EXTERNALLY-MANAGED marker beside the stdlib that makes `pip install` refuse —
including `pip install --user` — so the installer aborted on `set -e` with
`error: externally-managed-environment` before installing anything.

The installer now probes for that marker and, only when it is present AND this
pip supports the flag (pip 23.x+, the same pip that enforces PEP 668), exports
`PIP_BREAK_SYSTEM_PACKAGES=1` for its own subshell — so every `pip install
--user` call (component installs, the PEP 621 toolchain upgrade, native-ext
force-reinstalls) proceeds. Overriding the marker is safe here because ccenv
installs exclusively with `--user` into `~/.local` and never writes to the
system site-packages the marker protects. pipx (the usual PEP 668 fallback) is
deliberately NOT used: all five components share one `--user` site so the
`ccenvmcp` shim is importable across them, which pipx's per-app venvs would
break. Older pip (no marker, no enforcement) is untouched and never sees the
flag.

## v0.4.0

ccloop v0.8.0: keep an autonomous run alive across a model endpoint that isn't
ready yet — retry a failed **session launch** with increasing backoff instead
of stopping to ask.

**The bug.** ccloop's resilience work so far (v0.2.0 context wall, v0.3.0
API-error wedge) all watches the transcript, which assumes a session that
*started*. But when `claude` (or a `CCLOOP_CLAUDE_BIN` gateway) can't reach its
model **at launch** — `failed to fetch model list from … Connection refused`, a
local model server still booting, an auth blip — the child dies in ~0s **before
writing any transcript**. There is nothing to watch. ccloop mislabeled that as a
*no-progress* session: it burned one of `CCLOOP_STUCK_LIMIT` (default 3) strikes
and, in interactive mode, dropped to a blocking `Relaunch a fresh session? [Y/n]`
prompt — the opposite of autonomous. Three quick endpoint blips aborted the whole
run.

**The fix.** A launch failure is now its own class — `exit≠0` **and** no
transcript **and** no watcher relay — handled by retrying the *same* session
number with exponential backoff: `CCLOOP_LAUNCH_BACKOFF` seconds (default 5),
doubling, capped at `CCLOOP_LAUNCH_BACKOFF_MAX` (default 120), forever by default
(`CCLOOP_LAUNCH_RETRY_LIMIT` = 0). It never counts toward the no-progress limit
and never prompts; a watching human can Ctrl-C, and the run self-heals the moment
the endpoint returns. Absorbed retries don't advance the session count — only a
session that actually ran is logged — and the old "session 1 died fast → abort"
special case is subsumed (a cold endpoint at the start of a run is now waited out,
not fatal). New `launchfail` mode in the fake-claude test harness with
retry-then-abort and retry-then-recover tests; documented in ccloop `README.md`
and `DESIGN.md`.

## v0.3.0

ccloop v0.7.0: extend the "relay instead of wedge" guarantee from the context
wall to **transient API-error wedges**.

**The bug.** v0.2.0 made a full context window relay deterministically instead
of wedging at Claude Code's hard wall. But that wall is only *one* way a turn
ends in a committed `isApiErrorMessage` turn that then idles at the prompt. A
transient transport/API error — `API Error: The operation timed out.`, an
overload, a 5xx (common when `claude` points at a flaky or local model
endpoint) — aborts the turn, commits a *non-wall* `isApiErrorMessage` turn, and
sits there. It relayed neither (the wall detector matches only `Prompt is too
long`) nor fired the keepgoing Stop hook (the turn *aborted*, it did not
*end*). Confirmed in a real run: an interactive session wedged 21 minutes after
a model-endpoint timeout until a human typed into the TUI.

**The fix.** New `transcript.last_api_error()` returns the error text only when
a non-wall `isApiErrorMessage` turn is the *last real turn* (a newer
assistant/user/tool turn ⇒ ignored, so an error Claude Code retried past never
triggers a relay). The `run_session_interactive` watcher tracks how long the
same error has persisted at the tail and relays once it exceeds
`CCLOOP_API_ERROR_GRACE` seconds (default 60; 0 disables), giving Claude Code's
own retry first crack. Recovery reuses the proven relay path — `_build_prompt`
reads the resume file with no model call — so a fresh session restarts from
last-good state even while the endpoint is still degraded (it cycles and
recovers rather than dead-wedging). New tests in `tests/test_transcript.py`;
documented in ccloop `README.md` + `DESIGN.md`.

## v0.2.1

ccusage statusline: render the context-window size in whichever unit reads
cleanly. Local-model windows are powers-of-two multiples (262144 = 256*1024)
and were showing as the decimal "262.1k"; they now render in binary units as
"256k" (trailing ".0" stripped). Windows that aren't 1024-aligned — the
Anthropic 200000 / 1000000 windows — stay decimal so they read "200.0k" /
"1.0M" rather than an ugly binary "195.3k". The `used` token counter is
unchanged (still decimal). New `fmt_window()` in `ccusage/statusline.py`,
covered by `WindowFormatTests`.

## v0.2.0

ccloop v0.6.0 + ccusage v0.3.0: make "relay when the context fills" an actual
guarantee, and stop concurrent sessions from clobbering each other's usage
cache.

**The bug.** ccloop's entire reason for existing is that when a session's
context fills, it summarizes and restarts in a fresh session. In practice a
run could sail straight into Claude Code's hard wall ("Context limit reached ·
/compact or /clear to continue") and wedge there — in interactive mode with no
human to type `/compact`, forever. Two independent, each-sufficient causes,
both confirmed in a real wedged run:

1. The relay was driven *only* by a token `cutoff` compared against a usage
   reading. The cutoff is an absolute token count with no relationship to the
   model's real context window — set it at/above the window (or to a 1M-window
   default on a 200K model, or disable it) and `tokens >= cutoff` can never
   trip before the wall. Nothing clamped it.
2. The usage reading came from a single shared per-UID cache
   (`/tmp/ccusage-<uid>.json`). Any concurrent same-UID Claude Code session
   clobbered it, so a reader saw a foreign `session_id` and silently skipped
   the gate (fail-open) — no relay at all.

**The fix — react to the real wall event, not a predicted threshold.** When
the window fills with auto-compact disabled (ccloop always sets
`DISABLE_AUTO_COMPACT=1`), Claude Code injects a synthetic assistant turn into
the transcript flagged `isApiErrorMessage` with the text `Prompt is too long`.
That deterministic event is now what triggers the relay:

- Interactive: the watcher tails the transcript (`transcript.hit_context_wall`)
  and relays the moment that event appears — it previously watched only the
  hook's halt sentinel and could not see the wall at all.
- Headless `-p`: "Prompt is too long" *after* real work now relays (summarize +
  fresh session) instead of fatally aborting; it still aborts only when the fed
  handoff prompt itself is too big to start (no real assistant turn).
- Synthetic error turns are excluded from `assistant_turns` / `last_text` /
  `context_tokens` so they can't read as work or zero out the token figure.

The `cutoff` remains as a knob to relay *early*; it is no longer the only thing
standing between a session and the wall.

**Cache redesign (ccusage v0.3.0).** The statusline now writes a *per-session*
cache, `$XDG_STATE_HOME/ccusage/<session-id>.json` (default
`~/.local/state/ccusage/`), pruned after 2 days. Concurrent sessions can no
longer clobber each other; a reader keyed by its own `session_id` always finds
its own data. The MCP server reads the most-recently-written file. ccloop reads
its own session's file, with the legacy `/tmp/ccusage-<uid>.json` honored as a
transition fallback for sessions already in flight across the upgrade.

## v0.1.7

ccloop v0.5.1: fix the Stop-hook background-work wait gate wedging a session
forever.

The gate (`keepgoing._pending_background_task_count`) decided "a background
command is still running" by counting `*.output` files in the session's
`/tmp/claude-<uid>/<slug>/<sid>/tasks/` dir, on the assumption that the harness
deletes each file once it consumes the result. It does not — Claude Code never
reaps `tasks/*.output`; the files persist for the whole session (and beyond).
So once any background command had ever run, its orphaned `.output` re-fired
the gate on every subsequent Stop: the session could neither relay nor exit,
emitting "N background command(s) still running" until the context wall.

The gate now requires writer *liveness*, not file presence: an `.output`
counts only when a live process holds it open, read from `/proc/<pid>/fd`
(no subprocess, only on Stop, short-circuited once all paths match). Platforms
without procfs (macOS) fall back to an mtime freshness window
(`STALE_OUTPUT_SECONDS=90`) so a stale file can never fire the gate
indefinitely. Verified against the real wedged session: 5 leftover files
present → 0 counted.

## v0.1.6

ccmemory v0.11.0: memory anchors to the directory Claude Code was started in
(CWD) — nothing else. `project_root()` no longer walks up the tree and no
longer hunts for `.git/` or build-system markers (`pyproject.toml` /
`package.json` / `Makefile` / `Cargo.toml` / `go.mod`).

The old resolver walked up from CWD for those markers and only fell back to
CWD if it found none. That silently broke the autonomous-runner case: a ccloop
run dir (e.g. aitrader's `<data_dir>/run`, which holds `CLAUDE.md` +
`.claude/settings.json` but no `.git` and no build files) matched nothing, so
the walk ran off the top of `$HOME`, `project_root()` returned `None`, and
`memory_write` failed with "no memory dir resolvable" — never creating
`.ccmemory/` anywhere. It also meant a session started in a subdirectory had
its memory captured by a parent repo root instead of staying local.

What changed in ccmemory:

  - The anchor is now just CWD. A ccloop/autonomous run dir gets its own
    `.ccmemory/` right where it runs; a session started in a subdirectory keeps
    its memories local to that subdir (re-launching there finds them, and they
    never leak up to a parent). `project_root()`/`project_memory_dir()` are
    renamed `startup_dir()`/`startup_memory_dir()` to kill the misleading
    "go find the project" framing.
  - `PROJECT_MARKERS` and the walk-up loop are gone.
  - Both directory-relocation env vars — `CCMEMORY_PROJECT_ROOT` and
    `CCMEMORY_DIR` — are removed entirely. The store location is CWD, period;
    nothing overrides it. (`CCMEMORY_NO_AUTOMIGRATE` /
    `CCMEMORY_COMPILE_THRESHOLD` are behavior toggles, not store relocation,
    and are unaffected.)

## v0.1.5

ccmemory v0.10.0: memory compaction no longer uses `claude -p`. Anthropic is
moving the Claude Agent SDK, `claude -p`, and Claude Code GitHub Actions off
subscription usage onto a separate metered monthly credit pool (full API
rates, no rollover, capped per plan). The old `ccmemory compile` path shelled
out to a headless `claude -p` subprocess, so once that change lands every
compile run would burn metered credit. Compaction now runs in the LIVE
interactive session, which is unaffected by the billing change — zero
`claude -p`, zero credit, full LLM-quality synthesis.

What changed in ccmemory:

  - New `compile-memories` skill, installed to
    `~/.claude/skills/compile-memories/` by `install.sh`. It reads raw
    memories via the ccmemory MCP tools (`memory_list`/`search`/`get`),
    synthesizes one dense deduplicated `compiled-<topic>` article using the
    same compiler prompt as before, and writes it with `memory_write`. Its
    description carries trigger conditions so it auto-activates when relevant.
  - SessionStart hook appends a one-line compaction nudge when the
    *uncompiled backlog* — raw memories newer than the most recent
    `compiled-*` article — reaches `CCMEMORY_COMPILE_THRESHOLD` (default 20).
    Counting the backlog rather than the total keeps the nudge from firing
    forever, since compiled articles are additive and never delete raw notes.
    A skill with no trigger never gets invoked; this is its active trigger.
  - `compile.py` no longer calls any LLM. It exposes `count_backlog()` (hook)
    and `compile_status()` (CLI) plus the shared `COMPILER_PROMPT`. The
    `claude -p` subprocess, `_resolve_claude_bin`, and `CCMEMORY_CLAUDE_BIN`
    are gone.
  - `ccmemory compile` is now read-only: it reports the backlog, threshold,
    and candidate input names and points at the skill. `--dry-run` removed.

`install.sh` now heals native (compiled) dependencies stranded by a Python
version bump. Fixes the ccteam MCP failing to connect with
`ModuleNotFoundError: No module named 'watchfiles._rust_notify'` after the
system Python moved 3.9 → 3.14.

Root cause: with `PYTHONUSERBASE` set, Homebrew's `osx_framework_user`
scheme collapses the `--user` site to a SINGLE version-agnostic directory,
`$PYTHONUSERBASE/lib/python/site-packages`, shared verbatim by every Python
minor version (`python3 -c 'import site;print(site.getusersitepackages())'`
returns the same path under 3.13 and 3.14). Pure-Python packages survive a
Python upgrade there, but compiled extensions are ABI-tagged
(`watchfiles/_rust_notify.cpython-314-darwin.so`) and only load under the
matching interpreter. After a bump the old `cpython-39` `.so` lingers; the
new interpreter can't import it; and pip — seeing the distribution already
"present" in the shared dir — never refetches the right-ABI wheel.

New `heal_stale_compiled_exts()` runs after all components/overlays install:
it walks the shared user-site for `.so`/`.pyd`/`.dylib` files whose ABI tag
doesn't match the running interpreter's `EXT_SUFFIX` (`.abi3.so` and
untagged files are left alone), maps each stale file back to its owning pip
distribution via that dist's `RECORD`, and force-reinstalls the EXACT
installed version (`name==version`, `--force-reinstall --no-deps`, no
`--upgrade`) so the correct-ABI wheel lands without surprise upgrades of
packages ccenv doesn't own (the `--user` site is shared with the user's own
installs). Generic by construction — heals any compiled dep, self-heals an
already-broken box, near-instant no-op when every extension matches.

Also records the Python ABI cache tag (`sys.implementation.cache_tag`) in
`~/.config/ccenv/python-tag` so the next install can detect and announce a
Python bump. The actual heal keys off the on-disk `.so` files, not this
marker, so it still fixes a fresh checkout (no marker) or a box whose bump
predates this feature.

ccloop v0.5.0: headless `claude -p` now requires explicit, acknowledged
opt-in. Same billing driver as the ccmemory change — headless / Agent SDK
usage is moving onto a metered credit pool at API rates — but ccloop's
headless mode is intentional (autonomous unattended runs genuinely need
non-interactive `-p`), so it can't just be removed. Instead it can no longer
be entered *silently*:

  - `--headless` now requires `--accept-api-cost` as well; passing
    `--headless` alone is a usage error that explains the billing.
  - The old TTY auto-detect used to fall back to headless `-p` whenever
    ccloop ran without a terminal (cron, `nohup`, piped, backgrounded). That
    silent fallback is gone: no TTY + no `--headless --accept-api-cost` now
    **errors out** instead of quietly spending API credit. Interactive on a
    real TTY (subscription-billed) is unchanged and remains the default.
  - Mode resolution moved into `cli._resolve_interactive()` and is applied
    only to `run`/`resume`; `--list`/`--prune`/`install`/`--help` still work
    with no TTY.

## v0.1.4

`install.sh` auto-appends `PYTHONUSERBASE` (where needed) and a
runtime-guarded PATH-prepend for `~/.local/bin` to the shell's env file —
no more "REQUIRED: shell environment setup" copy-paste banner. Picks the
file sourced for non-interactive shells:

  - zsh  → `~/.zshenv` (sourced for ALL zsh invocations)
  - bash → `~/.bashrc` (no bash equivalent of zshenv; works for terminal-
                        launched claude since env inherits to subprocesses)

Two helpers: `ensure_env_var VAR VALUE` (skips if any existing
`export VAR=` line is present — never overrides the user's own setting),
and `ensure_env_path DIR` (writes a `case ":$PATH:" in *":$dir:"*) ;; *) export PATH="$dir:$PATH" ;; esac`
block so even when the env file is sourced repeatedly — nested subshells,
fresh terminals over a long session — `~/.local/bin` doesn't accumulate
in PATH). Each appended block is preceded by a `# [ccenv]` marker so
future install.sh runs (and humans) can identify what we put there.

Removed: the `rc_has` helper added earlier this version cycle (obsoleted
by the auto-append approach), the entire "REQUIRED: shell environment
setup" banner including the `need_pythonuserbase` / `need_path` gating
and the copy-paste one-liner generation, and the redundant
"USER_BIN is NOT on your shell PATH" warning in the verify step.

Per the [pythonuserbase-in-zshenv] memory: Windsurf writes its PATH
export to `~/.zshrc` — sourced only for interactive shells — and the
Claude Code hooks then fail with `ModuleNotFoundError`. ccenv 0.1.4 does
NOT repeat that mistake.

## v0.1.3

ccmemory v0.9.0: SessionStart protocol now MANDATES `memory_list()` as
the REQUIRED first action of every session, before responding to the
user's first message. v0.7.0 had steered the model toward it via
decision rules ("inventory? → list. topic? → search. body? → get."), but
concept/behavior memories (user preferences, conventions,
cross-cutting invariants) are not tied to any file path. The
PreToolUse-on-Read auto-injection that surfaces file-tied memories
never fires for them, so the model only learned of their existence if
it independently decided to query — which it usually didn't, because
it had no signal that anything was worth querying for. Result: lessons
captured into memory were re-derived from scratch in subsequent
sessions, and corrections the user had already applied got
re-litigated. Fixed in the SESSION_PROTOCOL text.

Also in 0.1.3: moved ccmemory's version history out of
`ccmemory/CLAUDE.md` and into a proper `ccmemory/CHANGELOG.md`, then
deleted `ccmemory/CLAUDE.md` entirely. Per-module `CLAUDE.md` files in
this repo are deprecated — only the top-level `/src/ccenv/CLAUDE.md`
(installed as the global `~/.claude/CLAUDE.md` rules file) should
exist; subdirectory architecture/install/test info belongs in that
subdirectory's `README.md`.

## v0.1.2

`install.sh` writes the bundle version to
`~/.config/ccenv/installed-version` on successful completion;
`instenv.prompt` reads it as the per-machine "what's actually installed
here" signal. Fixes a cross-machine version-check false positive
surfaced on the first multi-system update (0.1.0 → 0.1.1):

  $ <fire instenv.prompt>
  → "All six systems are current at v0.1.1 — no updates needed."

Reality: none of those six had actually re-run `install.sh`. They all
saw `/src/ccenv/VERSION` = 0.1.1 because `/src` is NFS-shared across
the cluster and the master's push updated the shared file in-place.
Their installed bits (pip packages, hooks, `~/.local/bin/...`) were
still at 0.1.0.

Root cause: confused "source code version" with "installed version."
The source tree is shared; the installed state is per-machine. Checking
the source `VERSION` file to decide "is this machine current?" is wrong
by construction in an NFS setup. Fix: write a per-machine marker only
`install.sh` updates.

## v0.1.1

ccloop wait gate fix. The previous "background-work wait gate" in
`ccloop/src/ccloop/keepgoing.py` was broken two ways that, together,
hung interactive sessions at the relay boundary:

  1. `return 0` is the wrong semantic in ccloop. In pure Claude Code a
     Stop hook returning 0 is a benign no-op. But ccloop's runner
     actively drives the session: when claude stops, the loop
     summarizes and relays. `return 0` = let the session END, which
     loses the running task.

  2. The wait gate ran BEFORE the cutoff gate, so the wait's `return 0`
     short-circuited the cutoff check. With a stale `.output` file
     in the tasks dir (harness reaps eventually, but not instantly),
     the wait gate would fire at the relay boundary, the cutoff gate
     never ran, the halt sentinel never got written, and the
     interactive watcher never SIGTERMed the TUI. Observed live:
     session hung at 270k/250k tokens with the model saying "I'm at
     the relay boundary — wrapping up." and then nothing.

Fix:
- Moved the wait gate to AFTER the cutoff gate. Cutoff always wins.
- Replaced `return 0` with `_emit_wait(n)` that emits
  `decision: block` with a minimal "Wait. Background command still
  running." re-feed. Session stays alive without the keepgoing
  CONTINUE_MSG "pick a new angle" push.
- Wait re-feeds intentionally do NOT bump the keepgoing counter and
  are NOT capped by `CCLOOP_MAX_CONTINUES` — that cap protects
  against model pathology, not external work.

New regression-guarding tests:
`test_cutoff_wins_over_pending_background`,
`test_pending_background_blocks_with_wait_message`,
`test_pending_background_does_not_bump_keepgoing_counter`,
`test_done_wins_over_pending_background`,
`test_pending_background_task_count_real_glob`.

## v0.1.0

Initial bundle VERSION. ccenv as a whole had no formal version string
until this — each component had its own pyproject version (ccloop
0.3.x, ccmemory 0.6.x, ccusage 0.1.x, ccteam 0.3.x, ccenvmcp 0.1.x)
but there was nothing at the umbrella level. Bare-semver one-line
file at `/src/ccenv/VERSION`, matching `ccteam/VERSION`'s format.
Read by `instenv.prompt` (locally via `cat`, remotely via
`raw.githubusercontent.com`) as the single source of truth for
cross-machine "is ccenv current?" checks.
