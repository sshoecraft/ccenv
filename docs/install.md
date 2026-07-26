# install.sh — the ccenv installer

`install.sh` installs the ccenv core components + overlay system into the user's
`--user` Python site and registers their MCP servers / hooks with Claude Code.
It is idempotent — re-running heals stale state rather than duplicating it.

See the header comment in `install.sh` for the component list, overlay
directories, and CLI flags (`--skip`, `--only`, `--no-overlays`).

## The bundled `CLAUDE.md` is the source of the base managed block

`assemble_ccenv_base_claude_md()` runs first, before any component, and
rebuilds the `# [CCENV MANAGED]` region of `~/.claude/CLAUDE.md` on every run:
marker header, a verbatim `cat` of this repo's top-level `CLAUDE.md`, any
overlay blocks, closing marker. Everything outside the markers
(component-owned sections like `[AWARENESS PROTOCOL]`) is preserved.

Consequence: an edit made directly inside the managed region of
`~/.claude/CLAUDE.md` lasts only until the next install run. Policy changes
must land in the repo's top-level `CLAUDE.md` — which doubles as this
project's own instructions file. v0.13.3 is the precedent: the temp-file rule
was rewritten in place on one box and had to be back-ported here to survive
reinstalls.

## The shared, version-agnostic `--user` site (a load-bearing gotcha)

`install.sh` forces `PYTHONUSERBASE=$HOME/.local` so `pip install --user` lands
binaries in `~/.local/bin` and packages under `~/.local/lib/...` on every
platform (see the long comment near the `export PYTHONUSERBASE` line for why —
Homebrew Python otherwise scatters scripts under `~/Library/Python/<ver>/bin`).

A consequence that matters for native deps: with `PYTHONUSERBASE` set, Homebrew's
`osx_framework_user` scheme resolves the user-site to a **single
version-agnostic** directory — `$PYTHONUSERBASE/lib/python/site-packages` — that
**every** Python minor version shares verbatim:

```
$ PYTHONUSERBASE=~/.local python3.13 -c 'import site;print(site.getusersitepackages())'
/Users/<you>/.local/lib/python/site-packages
$ PYTHONUSERBASE=~/.local python3.14 -c 'import site;print(site.getusersitepackages())'
/Users/<you>/.local/lib/python/site-packages   # same dir
```

Pure-Python packages survive a Python upgrade in that shared dir, but **compiled
extensions are ABI-tagged** (`foo.cpython-314-darwin.so`) and only load under the
matching interpreter. So a Python bump (3.9 → 3.14) leaves the old
`cpython-39` `.so` behind, the new interpreter can't import it, and pip — seeing
the distribution already "present" — never refetches the right-ABI wheel. The
observed symptom was the **ccteam MCP failing to connect** with
`ModuleNotFoundError: No module named 'watchfiles._rust_notify'`.

## `heal_stale_compiled_exts()` — the fix (v0.1.5)

Runs once, after all components and overlays are installed (so every compiled
dep is on disk). It:

1. Resolves the shared user-site (`site.getusersitepackages()`) and the running
   interpreter's `EXT_SUFFIX` (e.g. `.cpython-314-darwin.so`).
2. Walks the user-site for `.so` / `.pyd` / `.dylib` files whose filename carries
   a CPython/PyPy ABI tag that is **not** the current one. `.abi3.so` (stable
   ABI) and untagged files are left alone.
3. Maps each stale file back to its owning pip distribution by scanning every
   `*.dist-info/RECORD`, and reads the exact `Name`/`Version` from that dist's
   `METADATA`.
4. Force-reinstalls the **exact installed version**:
   `python3 -m pip install --user --force-reinstall --no-deps name==version`.
   The pin (and the absence of `--upgrade`) means the same release is rebuilt for
   the current ABI — never a surprise upgrade of a package ccenv doesn't own
   (the `--user` site is shared with the user's own `pip install --user`s).

It is generic (heals any compiled dep, not just `watchfiles`), self-heals an
already-broken box (it keys off the on-disk `.so` files, not any marker), and is
a near-instant no-op when every extension already matches.

Per-distribution failures (e.g. a release with no wheel for the new ABI and no
build toolchain) are warned and non-fatal.

## Markers written under `~/.config/ccenv/`

| file                | written by            | purpose |
|---------------------|-----------------------|---------|
| `installed-version` | end of `install.sh`   | the bundle VERSION actually installed on THIS box (NFS-safe; distinct from the shared source `VERSION`) |
| `python-tag`        | end of `install.sh`   | `sys.implementation.cache_tag` of the interpreter that ran the install, so the NEXT install can detect + announce a Python bump |
| `source.path`       | `gitsync` step        | absolute path of the checkout that ran `install.sh`, for the sync-status hook |

`python-tag` is informational only — `heal_stale_compiled_exts` decides what to
reinstall from the on-disk `.so` ABI tags, so it works even with no prior marker.

## MCP `alwaysLoad` — set v0.6.0, REMOVED v0.13.2

`install.sh` used to mark ccmemory's user-scoped entry `alwaysLoad: true`.
`strip_always_load()` now actively removes that field instead. It strips rather
than merely stops setting, because every box installed between v0.6.0 and
v0.13.1 already carries the flag in `~/.claude.json` — dropping the call alone
would have healed nothing.

### Why it went

**It never did what its name says.** Decompiled from binary 2.1.219,
`alwaysLoad` splits servers into two tiers launched in one `Promise.all`. The
flagged tier gets a *shared* 5000 ms deadline (`MCP_CONNECT_TIMEOUT_MS`), and
on expiry Claude Code starts the session anyway:

```
[MCP] regular-required: N/M not ready after 5000ms — proceeding; background connection continues
```

A deadline, not a barrier. So it never guaranteed the tools were present, which
was the whole reason for setting it.

**And on non-Anthropic models it removed them entirely.** Measured on a box
running Claude Code against `google/gemma-4-26B-A4B-it` through an
OpenAI-compatible proxy (visible as `chatcmpl-` tool-use IDs):

| Server | `alwaysLoad` | Outcome over 172 sessions / 3 weeks |
|--------|--------------|--------------------------------------|
| broker, journal, scheduler, searxng | unset | 353-495 successful calls each |
| ccusage | unset | used in 16 sessions, 0 rejections |
| **ccmemory** | **true** | **0 successful `memory_list` calls, ever** |

ccusage is the control that settles it: same bundle, same installer, same box,
same proxy, same session — the only difference is the flag, and it worked.

The ccmemory tools were not deferred behind `ToolSearch` either; no
deferred-tool system-reminder ever named them. They were simply absent from the
model's tool surface, while the server itself was demonstrably healthy
(`claude mcp list` reported ✔ Connected; the MCP handshake measured 0.18 s).

So: upside is a best-effort 5 s wait that guarantees nothing; downside is total
tool loss on an entire class of setup. Removed.

### What replaces it

Nothing, at the installer level. ccmemory's `SESSION_PROTOCOL` already carries
the software fallback — a call that *errors* means retry; a tool that is
*absent* means stop and tell the user rather than proceed as though the project
has no memory. A project that genuinely needs the tools present at turn 1 should
assert that in its own startup steps, where it can be checked and reported,
rather than relying on a flag that quietly proceeds degraded.

## History

- **v0.13.3** — bundled temp-file rule rewritten: test scripts and debug
  harnesses go in the project's `tests/` directory; /tmp only for true
  one-shot files (it is wiped on reboot and was losing work across sessions).
- **v0.13.2** — `strip_always_load()` replaces `enable_always_load()`: the flag
  is now actively removed from existing registrations. It was a 5 s deadline
  that proceeded degraded rather than a barrier, and it cost ccmemory its
  entire tool surface on non-Anthropic-model setups.
- **v0.6.0** — `enable_always_load()`: mark ccmemory (and, then, ccteam)
  `alwaysLoad: true`. Removed in v0.13.2, see above.
- **v0.1.5** — added `heal_stale_compiled_exts()` and the `python-tag` marker;
  fixes ccteam failing after a Python version bump (`watchfiles._rust_notify`).
- **v0.1.4** — auto-append `PYTHONUSERBASE` + a runtime-guarded `~/.local/bin`
  PATH-prepend to the shell env file (`~/.zshenv` / `~/.bashrc`).
- Earlier history: see `CHANGELOG.md`.
