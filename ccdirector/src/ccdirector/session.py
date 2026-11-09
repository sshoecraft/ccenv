"""Launch the director's session: one ccloop run, ccloop unmodified.

Everything ccdirector needs passes through ccloop's existing configuration:
- the director's system prompt as `--system-prompt-file`, through
  `CCLOOP_CLAUDE_EXTRA_ARGS` (aitrader's agent-run does the same). ccloop
  appends its own prompt with `--append-system-prompt-file`; the two coexist;
- the edit ban as permission deny rules, through `CCLOOP_SETTINGS` (passed to
  claude as `--settings`). Deny rules hold in every permission mode, including
  the `bypassPermissions` ccloop uses;
- the goal as ccloop's task, and the plan requirement as its criteria.

ccloop prints its run id only to stderr, which the interactive TUI shares, so
the run is found afterwards by a marker line in its task.md.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

PROMPT_FILE = Path(__file__).parent / "prompts" / "director.md"
MARKER = "ccdirector-run:"
#: The second-opinion advisors, by Claude Code's MCP tool naming.
CONSULT_TOOLS = "mcp__ask_.*"
#: ccloop's exit statuses when the user ended the run: no at its relaunch prompt,
#: and Ctrl-C.
USER_STOPPED = (3, 130)


class SessionError(RuntimeError):
    """The director's ccloop run could not be started or found."""


@dataclass
class Run:
    returncode: int
    folder: Path | None     # .ccloop/runs/<id>, None if ccloop never created one

    @property
    def run_id(self) -> str:
        return self.folder.name if self.folder else ""

    @property
    def stopped_by_user(self) -> bool:
        return self.returncode in USER_STOPPED


def hook_command() -> str:
    return f"{shlex.quote(sys.executable)} -m ccdirector.cli hook"


def consult_hook() -> dict:
    return {"matcher": CONSULT_TOOLS, "hooks": [
        {"type": "command", "command": f"{hook_command()} consult", "timeout": 30}]}


def deny_settings(root: Path) -> str:
    """Settings JSON that denies every edit under the project root, and records the
    director's consults.
    `//` starts an absolute path in a permission rule; `/x` would be relative to
    the settings source. Claude Code checks every file-editing tool (Edit, Write,
    NotebookEdit) against `Edit(...)` rules, so one rule covers all of them; a
    `Write(...)` or `NotebookEdit(...)` rule is never consulted and Claude Code
    warns at startup that it never matches."""
    target = "//" + str(root).lstrip("/") + "/**"
    return json.dumps({"permissions": {"deny": [f"Edit({target})"]},
                       "hooks": {"PostToolUse": [consult_hook()]}})


def environment(root: Path) -> dict:
    prompt = str(PROMPT_FILE)
    if any(c.isspace() for c in prompt):
        # ccloop splits CCLOOP_CLAUDE_EXTRA_ARGS on whitespace.
        raise SessionError(f"the director prompt path contains whitespace: {prompt!r}")
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    extra = env.get("CCLOOP_CLAUDE_EXTRA_ARGS", "").strip()
    env["CCLOOP_CLAUDE_EXTRA_ARGS"] = f"{extra} --system-prompt-file {prompt}".strip()
    if env.get("CCLOOP_SETTINGS"):
        raise SessionError("CCLOOP_SETTINGS is already set; ccdirector needs it for the "
                           "director's deny rules. Unset it and run again.")
    env["CCLOOP_SETTINGS"] = deny_settings(root)
    env["CCDIRECTOR_ROLE"] = "director"
    env["CCDIRECTOR_ROOT"] = str(root)
    env.pop("CCDIRECTOR_WORKER", None)
    return env


def ccloop() -> str:
    path = shutil.which("ccloop")
    if not path:
        raise SessionError("ccloop is not on PATH; install ccenv")
    return path


def mode_flags(headless: bool, model: str | None, cutoff: int | None = None,
               effort: str | None = None) -> list[str]:
    """ccloop flags. `cutoff` is ccloop's --cutoff, in thousands of tokens
    (0 = no early relay); None leaves ccloop's default, or on a resume the
    value the run was started with. `effort` is passed as ccloop's --effort,
    which wins over CCLOOP_EFFORT in the environment."""
    flags = ["--headless"] if headless else ["--interactive"]
    if model:
        flags.append(f"--model={model}")
    if cutoff is not None:
        flags.append(f"--cutoff={cutoff}")
    if effort:
        flags.append(f"--effort={effort}")
    return flags


def runs_folder(root: Path) -> Path:
    return root / ".ccloop" / "runs"


def find_run(root: Path, marker: str) -> Path | None:
    folder = runs_folder(root)
    if not folder.is_dir():
        return None
    for run in folder.iterdir():
        task = run / "task.md"
        if task.is_file() and marker in task.read_text(errors="replace"):
            return run
    return None


def start(root: Path, criteria: str, task: str, headless: bool = False,
          model: str | None = None, cutoff: int | None = None,
          effort: str | None = None) -> Run:
    """Run the director's ccloop in the foreground; return when ccloop exits."""
    marker = f"{MARKER} {uuid.uuid4()}"
    argv = [ccloop(), *mode_flags(headless, model, cutoff, effort), criteria,
            f"{task}\n\n{marker}\n"]
    done = subprocess.run(argv, cwd=str(root), env=environment(root))
    return Run(returncode=done.returncode, folder=find_run(root, marker))


def resume(root: Path, run: Run, feedback: str, headless: bool = False,
           model: str | None = None, cutoff: int | None = None,
           effort: str | None = None) -> Run:
    """Reopen a converged run with `feedback` at the top of its next prompt.

    ccloop checks convergence before building a prompt, so the criteria-met
    marker is removed first. The feedback goes into resume.md, which ccloop puts
    into the next session's prompt; the summarizer rewrites resume.md after that
    session, so it is seen once, which is all it is for."""
    if run.folder is None:
        raise SessionError("no ccloop run to resume")
    met = run.folder / "criteria-met"
    if met.exists():
        met.unlink()
    resume_file = run.folder / "resume.md"
    previous = resume_file.read_text(errors="replace") if resume_file.is_file() else ""
    resume_file.write_text(f"## From ccdirector\n\n{feedback}\n\n{previous}")
    argv = [ccloop(), *mode_flags(headless, model, cutoff, effort), "--resume-run", run.run_id]
    done = subprocess.run(argv, cwd=str(root), env=environment(root))
    return Run(returncode=done.returncode, folder=run.folder)
