"""ccloop — relay-loop wrapper for Claude Code.

Runs `claude -p` repeatedly. Between sessions, the prior session's
transcript is summarized into a resume file that is fed to the next
session as its prompt. The loop ends when the resume file is empty,
missing, or starts with DONE.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

# Read from installed package metadata, NOT hand-maintained here. This was a
# hardcoded string until 0.20.1 and it drifted two minor versions behind
# pyproject.toml (0.10.1 vs 0.12.0) across at least two releases — so
# `ccloop --version`, the one command you'd run to confirm an install took,
# reported the install had failed when it had succeeded. A version that lies is
# worse than no version when you are verifying a deploy.
#
# Deleting the second source of truth is the fix; remembering to bump both is
# not. Running from a source checkout with no installed dist is the one case
# with no metadata to read, and it says so rather than guessing.
try:
    __version__ = _dist_version("ccloop")
except PackageNotFoundError:
    __version__ = "0+unknown"
