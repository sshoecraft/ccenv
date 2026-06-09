#!/usr/bin/env python3
"""Install ccusage-mcp: pip-install the package, register the statusline and
MCP server in Claude Code's config.

UID 0  -> system-wide: /etc/claude-code/{managed-settings,managed-mcp}.json
UID !=0 -> user:        ~/.claude/settings.json + `claude mcp add --scope user`

Idempotent: re-running skips entries that are already correct, warns on
conflicts (existing entries that point elsewhere).
"""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
IS_ROOT = os.geteuid() == 0


def info(msg: str) -> None:
    print(f"==> {msg}")


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def write_json_atomic(path: Path, data: dict, mode: int = 0o600) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def update_json_key(
    path: Path, dotted_key: str, value, *, mode: int = 0o600, force: bool = False
) -> str:
    """Set path[dotted_key] = value.

    Default behavior: write if absent or matches; if a different value is
    already there, leave it alone. With force=True, overwrite unconditionally.

    Returns one of: "added", "matched", "conflict", "replaced".
    """
    data = load_json(path)
    parts = dotted_key.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    leaf = parts[-1]
    existing = cur.get(leaf)
    if existing is None:
        cur[leaf] = value
        write_json_atomic(path, data, mode=mode)
        return "added"
    if existing == value:
        return "matched"
    if force:
        cur[leaf] = value
        write_json_atomic(path, data, mode=mode)
        return "replaced"
    return "conflict"


def pip_install() -> Path:
    # macOS NFS/SMB/AFP shares create ._* AppleDouble sidecars for newly
    # written files. If pip builds in-place on such a share, setuptools picks
    # them up and emits duplicate .dist-info entries inside the wheel
    # ("multiple .dist-info directories found"). Stage the source into a local
    # tmpdir (skipping any existing sidecars) so the build runs on a clean
    # native filesystem.
    flags = [] if IS_ROOT else ["--user"]
    info(f"installing package via pip3 ({'system' if IS_ROOT else 'user'} scope)")

    def ignore_sidecars(_dir, names):
        return [n for n in names if n.startswith("._") or n == ".git"
                or n == "build" or n.endswith(".egg-info")]

    with tempfile.TemporaryDirectory(prefix="ccusage-build-") as tmp:
        staged = Path(tmp) / "src"
        shutil.copytree(REPO_ROOT, staged, ignore=ignore_sidecars)
        info(f"staged source at {staged} for clean build")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *flags, str(staged)],
            check=True,
        )
    scheme = "posix_prefix" if IS_ROOT else "posix_user"
    scripts_dir = Path(sysconfig.get_path("scripts", scheme=scheme))
    return scripts_dir


def find_console_script(scripts_dir: Path, name: str) -> Path:
    candidate = scripts_dir / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    on_path = shutil.which(name)
    if on_path:
        return Path(on_path)
    die(f"{name} not found after pip install (looked in {scripts_dir} and PATH)")


def install_user(mcp_bin: Path, statusline_bin: Path) -> None:
    settings_file = Path.home() / ".claude" / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    statusline_value = {"type": "command", "command": str(statusline_bin)}
    existing = load_json(settings_file).get("statusLine")
    result = update_json_key(settings_file, "statusLine", statusline_value, force=True)
    if result == "added":
        info(f"added statusLine to {settings_file}")
    elif result == "matched":
        info("statusLine already configured correctly")
    else:
        info(f"replaced statusLine in {settings_file} (was: {existing})")

    register_mcp_user(mcp_bin)


def register_mcp_user(mcp_bin: Path) -> None:
    if not shutil.which("claude"):
        warn("'claude' CLI not on PATH — skipping MCP registration.")
        warn(f"Run manually: claude mcp add --scope user ccusage {mcp_bin}")
        return
    listing = subprocess.run(
        ["claude", "mcp", "list"], capture_output=True, text=True
    )
    existing = ""
    for line in listing.stdout.splitlines():
        if line.startswith("ccusage:"):
            # Format: "ccusage: /path/to/bin  - ✓ Connected"
            after = line.split(":", 1)[1].strip()
            existing = after.split()[0] if after else ""
            break
    if not existing:
        subprocess.run(
            ["claude", "mcp", "add", "--scope", "user", "ccusage", str(mcp_bin)],
            check=True,
        )
    elif existing == str(mcp_bin):
        info("MCP server 'ccusage' already registered (user scope)")
    else:
        warn(f"MCP server 'ccusage' is registered pointing to: {existing}")
        warn("leaving as-is. To switch: claude mcp remove ccusage && re-run.")


def install_system(mcp_bin: Path, statusline_bin: Path) -> None:
    settings_file = Path("/etc/claude-code/managed-settings.json")
    mcp_file = Path("/etc/claude-code/managed-mcp.json")
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    statusline_value = {"type": "command", "command": str(statusline_bin)}
    existing = load_json(settings_file).get("statusLine")
    result = update_json_key(
        settings_file, "statusLine", statusline_value, mode=0o644, force=True
    )
    if result == "added":
        info(f"added statusLine to {settings_file}")
    elif result == "matched":
        info("statusLine already configured correctly")
    else:
        info(f"replaced statusLine in {settings_file} (was: {existing})")

    mcp_value = {"type": "stdio", "command": str(mcp_bin)}
    result = update_json_key(mcp_file, "mcpServers.ccusage", mcp_value, mode=0o644)
    if result == "added":
        info(f"registered ccusage in {mcp_file} (system-wide)")
    elif result == "matched":
        info(f"ccusage already registered in {mcp_file}")
    else:
        existing = load_json(mcp_file).get("mcpServers", {}).get("ccusage")
        warn(f"{mcp_file} has ccusage pointing to: {existing}")
        warn("leaving as-is. To switch, edit the file manually.")


def main() -> int:
    info(f"installing ccusage-mcp ({'system' if IS_ROOT else 'user'} scope)")
    scripts_dir = pip_install()
    mcp_bin = find_console_script(scripts_dir, "ccusage-mcp")
    statusline_bin = find_console_script(scripts_dir, "ccusage-statusline")
    info(f"ccusage-mcp:        {mcp_bin}")
    info(f"ccusage-statusline: {statusline_bin}")

    if IS_ROOT:
        install_system(mcp_bin, statusline_bin)
    else:
        install_user(mcp_bin, statusline_bin)

    print("\nDone. Restart Claude Code (or reload the session) to pick up the changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
