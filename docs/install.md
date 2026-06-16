# install.sh — the ccenv installer

`install.sh` installs the ccenv core components + overlay system into the user's
`--user` Python site and registers their MCP servers / hooks with Claude Code.
It is idempotent — re-running heals stale state rather than duplicating it.

See the header comment in `install.sh` for the component list, overlay
directories, and CLI flags (`--skip`, `--only`, `--no-overlays`).

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

## History

- **v0.1.5** — added `heal_stale_compiled_exts()` and the `python-tag` marker;
  fixes ccteam failing after a Python version bump (`watchfiles._rust_notify`).
- **v0.1.4** — auto-append `PYTHONUSERBASE` + a runtime-guarded `~/.local/bin`
  PATH-prepend to the shell env file (`~/.zshenv` / `~/.bashrc`).
- Earlier history: see `CHANGELOG.md`.
