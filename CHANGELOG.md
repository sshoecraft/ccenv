# ccenv changelog

Per the global rule: patch = fix, minor = feature, major = breaking.

## v0.15.0

**ccmemory 0.15.0: the settle stall announces itself before it is felt.**
v0.14.0 printed its notice from inside the stalling hook, where nothing could
show it — a launch with `CCMEMORY_MCP_SETTLE_SECONDS=10` simply froze for ten
seconds with no explanation.

A hook cannot describe its own stall: stdout is read only after the process
exits, hook stderr is never rendered anywhere in the UI (it goes to the
transcript JSONL alone), and a `/dev/tty` write is overdrawn by the TUI
repaint. Verified in a live transcript — `durationMs: 10246`, stderr
recorded, nothing on screen and nothing under ctrl+o. The stalling hook is
now silent; `systemMessage` from the notice hook is the only announcement.

The notice is now its own SessionStart hook — `ccmemory hook notice` — which
emits `systemMessage` and returns immediately while `ccmemory hook session`
sleeps. Silent whenever no stall is planned. Requires a reinstall: the entry
is new in `~/.claude/settings.json`.

Verified on 2.1.220 with a 10s stall: Claude Code runs both SessionStart
entries in parallel and flushes each `systemMessage` on its own hook's
completion — `notice` returned in 416 ms, `session` in 10132 ms, so the pane
showed `SessionStart:startup says: ccmemory: holding session start 10s …`
about 0.4s in, ahead of the wait it describes.

## v0.14.0

**ccmemory 0.14.0: opt-in MCP settle stall at SessionStart.**
`CCMEMORY_MCP_SETTLE_SECONDS=12` makes the SessionStart hook announce
`[ccmemory] waiting 12s for MCPs to settle…` and hold Claude Code that long
before returning, giving background MCP connects time to land before turn 1.

Unset by default, which is the point. The stall is a blind wall-clock wait —
no hook can gate on MCP status (SessionStart completes before the `init`
event that reports `mcp_servers`; no post-connect hook phase exists;
`alwaysLoad`, removed in v0.13.2, was a deadline that proceeds anyway). It
buys a probability while charging every fresh session on the box, so the
operator opts in per box rather than the bundle deciding for them.

Stalls only on `startup`/`resume`; `compact`/`clear` reuse the live process
and never wait. Unparseable or non-positive values are no-ops. See
`ccmemory/docs/mcp-settle.md`.

## v0.13.3

**Bundled temp-file rule rewritten: test scripts and debug harnesses go in the
project's `tests/` directory, not /tmp.** The old rule sent every test script
to /tmp, which is wiped on reboot — sessions kept reporting the scripts lost
and rebuilding them. /tmp remains the place only for genuinely one-shot files
that will never be used again.

The installed `~/.claude/CLAUDE.md` was already edited in place, but the
managed block is reassembled from the bundled `CLAUDE.md` on every install
(`assemble_ccenv_base_claude_md`), so without this source change the next
install run would have reverted the edit.

## v0.13.2

**`alwaysLoad` is removed.** `install.sh` set it on ccmemory from v0.6.0; it now
strips the field instead. `strip_always_load()` replaces `enable_always_load()`
and must actively rewrite the entry, not merely stop setting it — every box
installed since v0.6.0 already carries `"alwaysLoad": true` in `~/.claude.json`,
so dropping the call alone would have healed nothing.

Two independent reasons:

**It was never a barrier.** Decompiled from 2.1.219, the flag splits servers
into two tiers launched in one `Promise.all`; the flagged tier gets a shared
5000 ms deadline and on expiry Claude Code starts the session anyway
(`... not ready after 5000ms — proceeding; background connection continues`).
It never guaranteed the tools were registered, which was the entire point of
setting it. This was already known and recorded — it was not acted on.

**On non-Anthropic models it removed the tools entirely.** Measured on a box
running Claude Code against `google/gemma-4-26B-A4B-it` via an
OpenAI-compatible proxy, over 172 sessions and three weeks:

- ccmemory (`alwaysLoad: true`) — **0** successful `memory_list` calls, ever;
  122 transcripts carrying `No such tool available: mcp__ccmemory__memory_list`
- ccusage (same bundle, same installer, same box, flag unset) — used in 16
  sessions, **0** rejections
- broker / journal / scheduler / searxng (flag unset) — 353-495 calls each

The tools were not deferred behind `ToolSearch`; no deferred-tool reminder ever
named them. They were absent from the tool surface while the server itself was
healthy — `claude mcp list` showed ✔ Connected and the handshake measured
0.18 s. The SessionStart hook fired normally, which is why the model knew the
tool's name from the injected protocol text and called a tool it had never been
offered.

Upside: a best-effort 5 s wait that guarantees nothing. Downside: total tool
loss on a whole class of setup. Nothing replaces it at the installer level —
ccmemory's `SESSION_PROTOCOL` already handles an absent tool by stopping and
telling the user, and a project needing the tools at turn 1 should assert that
in its own startup steps where it can be checked and reported.

## v0.13.1

ccmemory v0.13.1: the "what to do when `memory_list` is unavailable" guidance
added in v0.13.0 told the model to call `ToolSearch` — a tool that only exists
when the harness has tool-search enabled, and so is frequently absent itself.
A model that correctly detected the missing `memory_list` then dead-ended
hunting for the tool with which to find a tool; one observed session spent a
turn running `bash` with its own reasoning pasted in as comments.

The step is now explicitly conditional, its absence is called out as normal
with an instruction to skip rather than search, shell hunting for MCP tools is
forbidden outright, and the terminal stop-and-tell-the-user state is reachable
from either branch.

## v0.13.0

**Breaking: three components leave the bundle.** `ccprospect` and `ccinsight`
are retired; `ccteam` moves to its own repository. `install.sh` no longer
builds, registers or hooks any of them.

Taken out of `install.sh`: the three component blocks, their entries in
`CORE_SUBDIRS`, `ccteam`'s `SessionStart` registration and NATS warning, the
`ccenvmcp` install gate (now `ccmemory || ccusage`), and the console-script
verify loop. 1108 -> 991 lines.

**`uninstall.sh` deliberately keeps all three.** An install only ever adds —
it cannot remove a hook, MCP registration, skill or state directory for a
component the bundle no longer knows about. Upgrading in place would leave
ccprospect/ccinsight/ccteam fully wired into every session, hooks firing,
against binaries that are still installed and so fail silently rather than
loudly. The upgrade path is therefore uninstall-then-install, run from the NEW
checkout:

    git pull && ./uninstall.sh && ./install.sh

Documented in README's new Upgrading section. A component removed from
`install.sh` must never be mirrored out of `uninstall.sh`.

**Fixed: `uninstall.sh` truncated its own documentation.** `docs/uninstall.md`
quotes both opening integration markers inside a ``` fence to show what they
look like. The stripper matched the first one, found no closing marker, and
applied its remove-to-EOF rule — deleting 52 of 112 lines. Two guards:

- Markers inside a fenced code block are documentation and are ignored. (Both
  `SKILL.md`s quote them the same way.)
- An opening marker with no close now leaves the file **untouched** and warns.
  The extent of such a block is unknowable; guessing at it is how a cleanup
  script destroys a file it was only supposed to trim.

Also in `install.sh`: comments that described ccteam's claim-before-edit as an
`alwaysLoad` justification, and ccinsight's sibling-library import convention,
are gone. `heal_stale_compiled_exts` stays — the hazard is the shared
`--user` site, which did not leave with ccteam.

## v0.12.1

Three `uninstall.sh` fixes found by running it for real, twice.

**The backup could hold the intermediate state, not the original.**
`backup_file()` copied unconditionally, so a file rewritten more than once in
a single run ended up with a "backup" of a partially-uninstalled state. Seen
on `~/.claude/CLAUDE.md`: the ccproject step strips the `[AWARENESS PROTOCOL]`
section, the global step then strips the `[CCENV MANAGED]` region, and the
second `backup_file` overwrote the first — leaving a backup with no awareness
section in it. (`~/.claude.json` and `settings.json` have the same shape,
touched once per component.) `backup_file()` is now a no-op once the backup
exists, so `<file>.uninstall-bak.<stamp>` is always the pre-uninstall state.
The now-redundant `backup_claude_json_once` helper is gone.

**Every idempotent re-run littered `$HOME` with identical backups.** The
backup fired on the ATTEMPT to modify a file, not on an actual change, so a
second full run — which correctly no-ops everything — still produced fresh
copies of `settings.json`, `~/.claude.json` and `.bashrc`. The shell callers
already grep-gate before backing up (which is why `CLAUDE.md` was exempt);
the three Python writers now call `bak()` themselves immediately before
`os.replace`, where the decision to write has actually been made. A no-op run
now writes nothing at all.

**`~/.claude/hooks/` was left behind empty** after `check_sync_status.sh` came
out. gitsync now `rmdir`s it — which fails harmlessly, by design, if anything
else put a hook there.

## v0.12.0

`uninstall.sh` — the exact inverse of `install.sh`. Until now ccenv could
only be added to a machine; backing it out meant hand-editing four JSON
files, hunting skill directories, and remembering which `pip` dists exist.

For each component in scope it removes the pip distribution, its hook
entries in `~/.claude/settings.json`, its user-scope MCP registration in
`~/.claude.json`, its skill directory, its managed region in
`~/.claude/CLAUDE.md`, and its per-project state.

Same component vocabulary and `--only` / `--skip` flags as `install.sh`:
`ccproject gitsync ccmemory ccprospect ccinsight ccusage ccloop ccteam
ccenvmcp`.

Three things it does that a naive script would get wrong:

- **Hook removal matches on the EXECUTABLE, not a substring.** The first
  token of the command must be the component's console script (for
  ccproject, `awareness_hooks.py` must be the script argument). A foreign
  hook that merely mentions the word is never touched, and when one of ours
  shares a settings entry with a foreign hook, only our hook object is
  dropped.
- **Injected project blocks are found without a filesystem scan.**
  `prospect-integrate` / `ccinsight-integrate` land marker-fenced blocks in
  arbitrary project files (a CLAUDE.md, a ccloop criteria file, a custom
  loop's constitution fragment). Candidates come from `~/.claude.json`'s
  `projects` map — the bounded, authoritative list of directories a session
  ever ran in — searched at depth <= 2 for markdown, plus the exact
  `binding_file` each `integration.json` recorded, which may sit deeper.
  `--project DIR` adds a repo that was integrated but never opened here.
- **Nothing is deleted without a copy first.** Rewritten files are copied to
  `<file>.uninstall-bak.<stamp>`; every per-project state directory is
  tarred into `~/ccenv-uninstall-<stamp>/` before removal.

Project-data policy: `.ccprospect/`, `.ccinsight/` and `.ccteam/` are
archived then deleted (`--keep-project-data` opts out). `.ccmemory/` is
NEVER touched — it is committed repo content that travels with the repo.
`.ccloop/` run state is left in place and merely listed.

`ccenvmcp`, the shared shim every other component imports, is uninstalled
last and only once no dependent dist remains.

Also: `--dry-run` prints every action and changes nothing, a confirmation
prompt guards the project-level work (`-y` to skip), the global artifacts
(the `[CCENV MANAGED]` CLAUDE.md region, `~/.config/ccenv`, the shell
exports) come out only on a fully unscoped run, and the `# [ccenv]`
`~/.local/bin` PATH guard is kept by default since unrelated `pip --user`
tools depend on it (`--remove-path` drops it).

## v0.11.1

ccloop v0.10.1: the interactive relay now terminates the session's whole
process tree, fixing `claude` processes that accumulated one per relay.

Observed in production under one `ccloop --resume-run`: four live `claude
... begin` processes for sessions 10, 11, 12 and 13 — elapsed 3h17m, 2h50m,
1h21m and 47m — i.e. every session the run had ever relayed was still
running, all pointed at the same run state.

Cause: `CCLOOP_CLAUDE_BIN` is commonly a shell wrapper that exports env (base
URL, model, token budget) and then runs `claude "$@"`, so the PID ccloop
tracks is bash, not claude. The relay's `proc.terminate()` went to bash, and
a non-interactive bash does not forward SIGTERM to the foreground job it is
waiting on — bash exited, and claude was reparented to systemd and left
running forever. Runs that invoke `claude` directly were never affected,
which is why this survived the earlier orphan fix (v0.6.1's
`PR_SET_PDEATHSIG`, which likewise only protects the *tracked* child).

Fix: on relay, `run_session_interactive` snapshots the child's descendants
from `/proc` *before* signalling — a wrapper's worker stops being a
descendant the moment the wrapper dies — then escalates SIGTERM → SIGKILL
across the whole tree, deepest-first. Each PID is pinned to its `/proc` start
time, so a recycled PID can never be signalled. The headless path already
covered this via `start_new_session=True` + `killpg`; the interactive path
can't use that without costing the TUI its controlling terminal.

Also fixes a pre-existing flake in the PDEATHSIG regression test: the autouse
`no_sleep` fixture made its counted retry loop wait no wall-clock time at
all, and it read `/proc/<pid>` existence as liveness when an unreaped zombie
still has an entry.

## v0.11.0

ccmemory v0.13.0: the SessionStart protocol now handles `memory_list` being
**unavailable**, not just present-and-working.

MCP servers connect in the background as a session starts, so ccmemory's
tools may not be registered by turn 1. Claude Code offers no mechanical gate
for this — verified against 2.1.219: SessionStart hooks complete *before* the
`init` event that reports MCP status (so a hook cannot observe it), and
`alwaysLoad:true` is a timeout rather than a barrier — its tier waits
`MCP_CONNECT_TIMEOUT_MS` (default 5000, shared across the tier) and then
proceeds degraded. Upstream has no post-MCP-connect hook phase
(anthropics/claude-code#26112).

The hole: an unregistered MCP tool raises no error, it is simply absent from
the tool list — identical in appearance to a project with no ccmemory. The
session proceeds, and every "no prior memory on this" conclusion is silently
wrong. The protocol now distinguishes call-errors (retry 3x) from
tool-absent (`ToolSearch` once, then STOP and tell the user).

## v0.10.0

New component: **ccinsight 0.1.0** — observation-to-hypothesis memory (the
EMERGING store, third sibling alongside ccmemory's TIMELESS and
ccprospect's FUTURE). Built after checking ccprospect against the exact
complaint that motivated it (an agent that narrates events but never forms
a higher-level pattern) and finding it didn't solve it: an audit of one
trading agent's 374-entry journal found heavy price-action language but
zero pattern-recognition hypotheses, because nothing in its loop ever
forced synthesis — ccprospect, a flawless mechanical store, had nothing
pattern-shaped to catch. Two independent model consultations (`ask_fable`,
`ask_gpt`) converged: the storage is the easy part; the forcing function is
the load-bearing 80%.

An append-only, uncapped observation buffer (`.ccinsight/observations.jsonl`)
feeds versioned DERIVED views — symbolic-key, temporal-window, metric-
correlation, and motif-candidate (the last exists specifically because a
same-key-recurrence trigger is circular for a perception domain: a chart
wedge has no repeated discrete event to key by before it's recognized).
Six pluggable mechanical trigger families decide WHEN synthesis is
required; every fired trigger gets a schema-gated forced response — a
falsifiable candidate hypothesis (preregistered test, cited evidence), a
no-actionable-pattern disposition (must prune the cluster, not merely
decline), or a bounded insufficient-coverage disposition (must name a
concrete next-observation requirement) — no fourth option. Hypotheses are
immutable (`.ccinsight/hypotheses/*.md`); epistemic status AND confidence
are both derived by folding an append-only evidence ledger
(`events.jsonl`) — there is no field anywhere for a model to self-assert a
confidence score, and `mark_supported`/`mark_refuted` are mechanically
refused without the matching outcome already on record. Graduation into
ccmemory is gated (supported status only), owns dedup, carries provenance,
and auto-spawns a ccprospect re-verification contract — never automatic,
since a confabulated pattern laundered into the one store with no expiry
and auto-injection was the single biggest risk both consultations flagged.

Reuses ccprospect's predicate engine and ccmemory's store as sibling
libraries (install-order dependencies, same convention as ccenvmcp — never
declared pip dependencies, since this repo installs from local source).
MCP tools: insight_observe/survey/hypothesize/ledger/dispose/amend/
graduate/list/get/report; hooks (SessionStart evaluate+inject, PostToolUse
mechanical observation harvest, Stop INSIGHT.md regen, PreToolUse guard)
autoinstall on MCP boot. 119 tests plus three independent end-to-end
verifications (in-process lifecycle, cross-store graduation writing a real
ccmemory file + ccprospect contract, and a full stdio JSON-RPC drive of the
actual MCP server subprocess).

install.sh: ccinsight added to core components (pip install, MCP register
— deliberately without alwaysLoad, same reasoning as ccprospect), added to
the ccenvmcp foundation gate (it also imports ccmemory + ccprospect
directly), CORE_SUBDIRS, and the verify loop; installed after both ccmemory
and ccprospect so its sibling-library imports resolve.

Also ships the `ccinsight-integrate` skill (installed to
`~/.claude/skills/ccinsight-integrate/`, `prospect-integrate` pattern):
one-time per-project wiring of the pending-synthesis binding, landed
adjacent to (never merged with) an existing ccprospect integration since
the two answer different questions. Same deterministic classify tree
(interactive / ccloop / custom-loop with owner-confirmed diffs against
constitution SOURCE, never hot-edited). Decision recorded in
`.ccinsight/integration.json`.

**Also this release — two ccprospect 0.2.0 fixes**, root-caused from the
same investigation: itrader's real `.ccprospect/events.jsonl` showed 8
contracts filed between 00:33–06:20 UTC, exhausting the daily budget almost
7 hours before market open because the reset boundary is UTC midnight,
which splits a 24-hour trading agent's overnight session in half. Added
`CCPROSPECT_DAY_RESET_TZ` (realign the reset to a project's actual
operating day; default UTC behavior unchanged) and raised
`DEFAULT_DAILY_BUDGET` 8 → 20. See `ccprospect/CHANGELOG.md` v0.2.0.

## v0.9.1

**ccprospect 0.1.1** — removed dead design-archive path references from
ccprospect's `CHANGELOG.md` and `docs/ccprospect.md`: the archive lives
outside this repo, so a hardcoded path can never resolve for a clone. No
functional change. See `ccprospect/CHANGELOG.md` v0.1.1.

## v0.9.0

**ccmemory 0.12.0** — fixes unbounded context growth from the `PreToolUse:Read`
inject hook re-surfacing the same memory teaser on every Read with no memory
of what it had already shown (measured: 55% of one long ccloop session's
context on `/src/aitrader`'s memory store). Adds a session-scoped injection
ledger (atomic per-Read claim against a new `injection_ledger` table in the
existing `index.db`), a hard per-session cap (20 unique slugs / ~4000 est.
tokens, both env-tunable), fail-shut behavior on any ledger error or missing
`session_id`, and ledger reset on SessionStart `compact`/`clear`. `Store` now
runs in WAL mode. Verified this resets for free on every ccloop relay (fresh
`session_id` per relay, never `--resume`) with no ccloop-specific code
needed. See `ccmemory/CHANGELOG.md` v0.12.0 for full detail.

## v0.8.0

New component: **ccprospect 0.1.0** — prospective memory (the FUTURE store,
sibling of ccmemory's TIMELESS store), implementing Part IV of the design
archived in `prospect.md` / `prospective-memory-ledger-design.md`.

Immutable intention/forecast contracts (`.ccprospect/contracts/*.md`, YAML
frontmatter) + an append-only `events.jsonl`; all state (attention ×
resolution) is derived by folding the log, so outcomes cannot be rewritten.
Typed predicates v1 (`at`, `session_start`, `path_exists`, `path_changed`,
`cmd_ok`, `cmd_fail`, `cmd_match`) are evaluated at wake boundaries — the
SessionStart hook injects a PROSPECT INBOX of fired/due items with
mechanically observed values; no daemon. Creation refuses already-true
predicates (probes baseline once at creation), enforces attention budgets
(~20 active, 8/day, env-tunable), and is gated while fired items sit
unacknowledged. `prospect_amend` supersedes; cancelled/superseded contracts
still resolve counterfactually at their original expiry (final evaluation at
the boundary). Optional `expect` + probability bucket (20/40/60/80) upgrade
an intention to a forecast feeding the factual `prospect_report`
(denominators always; no adjectives, thresholds, or advice). MCP tools:
prospect_file/inbox/ack/amend/list/get/report; hooks (SessionStart evaluate+
inject, Stop PROSPECT.md regen, PreToolUse guard on contracts/ +
events.jsonl + PROSPECT.md) autoinstall on MCP boot. 72 tests plus an
end-to-end stdio MCP drive.

install.sh: ccprospect added to core components (pip install, MCP register —
deliberately without alwaysLoad since the SessionStart hook does the
wake-time work independently of MCP; hooks registered at install so the
first session already gets the inbox), added to the ccenvmcp foundation
gate, CORE_SUBDIRS, and the verify loop.

Also ships the `prospect-integrate` skill (installed to
`~/.claude/skills/prospect-integrate/`, ccmemory/compile-memories pattern):
one-time per-project wiring of the inbox binding. Deterministic decision
tree — interactive projects get a short managed block in project CLAUDE.md;
ccloop projects get the forced-step block (NOT-DONE grammar,
`pending_count == 0` completion, NO_CONTRACT always legal) in the criteria
file; custom-loop projects (own constitution) are never guessed at: the
skill locates the prompt artifact via project docs/memory, confirms the
target with the user, and for built constitutions produces a diff against
the source rather than editing a generated file. The decision is recorded in
`.ccprospect/integration.json` (committed with the repo) so re-runs refresh
the managed block in place.

## v0.7.0

ccloop 0.10.0: added `--effort=LEVEL` CLI flag, mirroring the existing
`--model=NAME` flag. Previously the reasoning-effort level for spawned
`claude` sessions could only be set via the `CCLOOP_EFFORT` env var; now
it can also be passed directly on the command line (e.g.
`ccloop --effort=max ...`), and the flag wins over `CCLOOP_EFFORT` just
like `--model` wins over `CCLOOP_MODEL`. Threaded through
`cli.py` (`_extract_effort`, following the `_extract_model` pattern) into
`runner.cmd_run`/`cmd_resume`/`loop`, which override `cfg["effort"]`
before `_build_command` appends `--effort` to the spawned `claude`
invocation. Added matching CLI-parsing and end-to-end (`fake_claude`
argv-capture) tests.

## v0.6.1

ccloop 0.9.1: fixed orphaned `claude` processes surviving after ccloop
itself dies.

The `claude` child that `run_session()`/`run_session_interactive()` spawn
now sets `PR_SET_PDEATHSIG` (via `preexec_fn`) so the kernel SIGTERMs it
automatically if the ccloop process that spawned it dies for any reason —
crash, `kill -9`, OOM-kill — not just ccloop's own graceful relay/interrupt
handling. Previously, a `claude` child spawned with inherited (non-piped)
std fds had no death-of-parent protection: if ccloop died outside its own
signal handling, the child was simply reparented to init and kept running
indefinitely — the actual mechanism behind long-lived orphaned
`claude ... begin` processes with PPID 1 seen in the wild. Confirmed and
regression-tested by killing the ccloop process directly and asserting its
child process dies with it. (The in-run relay path itself — halt sentinel
/ context wall triggering `proc.terminate()` — was verified separately to
already clean up its own child correctly across 5 live relay cycles
against a real `claude` binary; that was not the source of the leak.)

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
