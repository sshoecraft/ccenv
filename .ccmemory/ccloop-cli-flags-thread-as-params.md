---
name: ccloop-cli-flags-thread-as-params
description: ccloop CLI flags thread as explicit params (cli→cmd_run/cmd_resume→loop), flag beats CCLOOP_* env; fake_claude FAKE_ARGS_FILE asserts spawned argv.
metadata:
  type: project
---

# ccloop CLI flags: how they thread, and how to add the next one

Established with `--model` (ccloop 0.9.0 / bundle 0.5.0). ccloop's runtime
config is env-driven (`_config()` reads `CCLOOP_*`), but CLI flags do NOT
mutate `os.environ` — they thread as explicit keyword parameters:

    cli.main → runner.cmd_run / cmd_resume → runner.loop(..., model=None)

and `loop()` overrides the cfg dict after `_config()`:

    cfg = _config()
    if model:
        cfg["model"] = model   # flag wins over CCLOOP_MODEL

## Recipe for the next value flag (e.g. --effort)

1. `cli.py`: `_extract_value_flag(argv, flag, what)` is the generic popper
   (handles `--flag=V` and `--flag V`, last occurrence wins, missing-value
   error). `_extract_cutoff` and `_extract_model` are thin wrappers over it —
   add validation in the wrapper (`--model` rejects empty/whitespace values
   so `--model=` doesn't silently no-op via the falsy `if cfg[...]` check in
   `_build_command`).
2. Thread through BOTH dispatches (`cmd_run` and `--resume-run`'s
   `cmd_resume`) — extraction happens before dispatch so it applies to both.
3. Update the USAGE string (Options section + the env var's line gets
   "(--x flag wins over this)").
4. Semantics: per-invocation, same as the env var — a resume does NOT
   remember the model the run was started with (unlike `--cutoff`, which
   persists in `<run-dir>/cutoff` because the guard/keepgoing HOOKS in other
   processes must read it; model is consumed only in the runner process).

## Testing pattern

- `tests/fake_claude.py` honors `FAKE_ARGS_FILE`: appends each invocation's
  argv as a JSON line. Use it to assert end-to-end what reached the claude
  command line (e.g. flag-beats-env: set `CCLOOP_MODEL=sonnet`, pass
  `model="opus"`, assert `argv[argv.index("--model")+1] == "opus"`).
- cli-level parse tests stub `runner.cmd_run`/`cmd_resume` with fakes that
  capture kwargs (`_stub_run`/`_stub_resume` in test_cli.py) — keep their
  signatures in sync when adding a param.
- Do NOT write a seam test whose fake reimplements the override logic — that
  tests the test. Use the FAKE_ARGS_FILE end-to-end path instead.
