# uninstall.sh

The inverse of `install.sh`. Backs ccenv out of a machine — packages, hooks,
MCP registrations, skills, managed CLAUDE.md regions, and the blocks the
`*-integrate` skills injected into project files.

## Usage

```
./uninstall.sh                       # remove EVERYTHING
./uninstall.sh --only ccprospect     # remove one component (repeatable)
./uninstall.sh --skip ccmemory       # remove all but one (repeatable)
./uninstall.sh --dry-run             # print every action, change nothing
./uninstall.sh -y                    # skip the confirmation prompt
./uninstall.sh --keep-packages       # leave the pip dists installed
./uninstall.sh --keep-project-data   # do not touch per-project state dirs
./uninstall.sh --remove-path         # also strip the ~/.local/bin PATH guard
./uninstall.sh --project DIR         # also clean DIR (repeatable)
```

Components: `ccproject gitsync ccmemory ccprospect ccinsight ccusage ccloop
ccteam ccenvmcp`.

That list is everything ccenv has **ever** installed, which is deliberately
wider than what the current `install.sh` ships — `ccprospect`, `ccinsight` and
`ccteam` were removed from the bundle in v0.13.0.

This is the point of the script. `install.sh` only ever adds; it cannot remove
a hook, MCP registration, skill or state directory belonging to a component it
no longer knows about. So the documented upgrade path is uninstall-then-install,
run from the NEW checkout:

```sh
git pull && ./uninstall.sh && ./install.sh
```

A component dropped from this script would strand its hooks and registrations
on every box that ever ran the older bundle, with nothing able to clean them
up. Removals from `install.sh` must therefore never be mirrored here.

## What comes out, per component

| Component  | pip dist      | settings.json hooks                                   | MCP          | skill dir                       | other |
|------------|---------------|-------------------------------------------------------|--------------|---------------------------------|-------|
| ccproject  | —             | PostToolUse/Stop/SessionStart `awareness_hooks.py`     | —            | `project-awareness`             | `[AWARENESS PROTOCOL]` section of `~/.claude/CLAUDE.md` |
| gitsync    | —             | SessionStart `check_sync_status.sh`                    | —            | —                               | `~/.claude/hooks/check_sync_status.sh`, `~/.config/ccenv/source.path` |
| ccmemory   | `ccmemory`    | `hook session\|stop\|guard\|inject`                    | `ccmemory`   | `compile-memories`              | project `.ccmemory/` **preserved** |
| ccprospect | `ccprospect`  | `hook session\|stop\|guard`                            | `ccprospect` | `prospect-integrate`            | injected blocks + `.ccprospect/` |
| ccinsight  | `ccinsight`   | `hook session\|posttool\|stop\|guard`                  | `ccinsight`  | `ccinsight-integrate`           | injected blocks + `.ccinsight/` |
| ccusage    | `ccusage-mcp` | — (owns `statusLine`)                                  | `ccusage`    | —                               | `/etc/claude-code/managed-*.json` when root-installed |
| ccloop     | `ccloop`      | PostToolUse `guard`, Stop `keepgoing`                  | —            | —                               | `.ccloop/` run state **left in place**, listed |
| ccteam     | `ccteam`      | SessionStart `ccteam session-start`                    | `ccteam`     | —                               | `.ccteam/` |
| ccenvmcp   | `ccenvmcp`    | —                                                      | —            | —                               | removed LAST, and only once no dependent dist remains |

Global artifacts — the `[CCENV MANAGED]` region of `~/.claude/CLAUDE.md`,
`~/.config/ccenv/`, and the `# [ccenv]` shell exports — come out only on a
fully unscoped run. A partial (`--only`/`--skip`) run leaves them alone,
because they belong to the bundle rather than to any one component.

## Design notes

### Hook matching is by executable, not substring

`settings.json` is shared with the user's own hooks and with other tools. A
`grep`-and-delete would eat anything containing the string `ccmemory`. The
matcher instead requires the command's FIRST token to basename to the
component's console script, and its LAST token to be one of that component's
known subcommands (`awareness_hooks.py` is matched as a script argument since
ccproject's hooks run under `python3`). When one of our hooks shares a
settings entry with a foreign hook, only our hook object is dropped and the
entry survives with the rest intact.
### Injected blocks are found without scanning the filesystem

`prospect-integrate` and `ccinsight-integrate` land marker-fenced blocks:

```
<!-- [CCPROSPECT INTEGRATION] managed by prospect-integrate; edit via the skill -->
<!-- [CCINSIGHT INTEGRATION] managed by ccinsight-integrate; edit via the skill -->
```

The target file is whatever that project's binding surface is — a `CLAUDE.md`,
a ccloop criteria file, or a custom loop's constitution fragment several
directories deep. There is no global registry of them.

A root-anchored `find` is both banned by this repo's rules and the wrong tool:
a block can only exist somewhere a session actually ran. So candidates come
from `~/.claude.json`'s `projects` map — the bounded, authoritative list of
directories Claude Code has opened — and within each one:

- markdown at depth <= 2 (covers `CLAUDE.md`, `docs/*.md`, `.claude/*.md`,
  criteria files), plus
- the exact `binding_file` recorded in `.ccprospect/integration.json` /
  `.ccinsight/integration.json`, which may sit deeper.

`--project DIR` adds a repo that was integrated but never opened on this
machine.

Removal is deterministic — everything from the opening marker through the
closing marker is dropped and the resulting blank-line run collapsed.

Two guards, both added in v0.13.0 after this script truncated its own
documentation:

- **A marker inside a fenced code block is documentation, not an injection.**
  This file and both `SKILL.md`s quote the opening markers in ``` fences to
  show what they look like. The stripper tracks fences and ignores anything
  inside one.
- **An opening marker with no closing marker leaves the file untouched.** It
  previously removed to EOF, which is how the fenced pair above — two opening
  markers, no close — ate 52 lines of this document. The extent of such a
  block is unknowable, so the script now refuses to guess and warns for a
  human to look at it.

### Nothing is deleted without a copy first

- Rewritten files -> `<file>.uninstall-bak.<stamp>` beside the original.
- Per-project state directories -> `~/ccenv-uninstall-<stamp>/<mangled>.tar.gz`
  before `rm -rf`. If the tar fails, the directory is left in place.

A state directory can hold months of accumulated contracts or observations
with no other copy; a plain `rm -rf` would be unrecoverable for anything the
project never committed. `git` is never used, to restore or otherwise.

The backup has two non-obvious rules, both of which were wrong in v0.12.0:

- **Once per file per run.** Several files are rewritten more than once
  (`CLAUDE.md` loses the component section, then the managed region;
  `settings.json` and `~/.claude.json` are touched per component). Re-copying
  would leave a backup of a half-uninstalled state.
- **Only when a change is actually about to happen**, never on the mere
  attempt — otherwise every idempotent re-run litters `$HOME` with identical
  copies. The shell callers grep-gate first; the embedded Python writers call
  `bak()` immediately before `os.replace`, where the decision to write has
  been made.

### `.ccmemory/` is never touched

It is committed repo content that travels with the repo — deleting it would
cost the user memory on every other clone. The uninstaller reports how many
projects still carry one and stops there. There is deliberately no flag to
override this.

### `ccenvmcp` comes out last

Every other MCP component imports the shim. Removing it earlier would leave
the others broken mid-uninstall, so it is gated on no dependent dist
remaining — if any survive (`--keep-packages`, a failed `pip uninstall`), the
shim is kept and the script says to re-run with `--only ccenvmcp`.

## Verified behavior

A full run on a fully-installed box removed: 19 hook entries across four
events, the `statusLine`, 5 MCP registrations, 4 skill directories, 7 pip
dists, both managed CLAUDE.md regions, `~/.claude/hooks/`, and
`~/.config/ccenv/`. Preserved: the user's own `[SEARCH DISCIPLINE]` CLAUDE.md
section byte-for-byte, their 4 non-ccenv MCP servers, their non-ccenv skills,
every non-hook key in `settings.json`, the `~/.local/bin` PATH guard, and all
22 `.ccmemory/` directories.

A second full run immediately after is a complete no-op: every step reports
not-present / not-registered / not-installed, no warnings, and no files are
written — including no backups.

## History

- **v0.13.0** — fenced-code markers are ignored and unterminated blocks leave
  the file untouched (this script had truncated its own docs). Retains
  `ccprospect` / `ccinsight` / `ccteam` after their removal from `install.sh`,
  making uninstall-then-install the documented upgrade path.
- **v0.12.1** — backup is once-per-file and only on real changes; gitsync
  removes the emptied `~/.claude/hooks/`.
- **v0.12.0** — created.
