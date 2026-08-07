"""ccmemory - persistent memory for Claude Code, file-of-truth + FTS5 index."""

from importlib.metadata import PackageNotFoundError, version as _dist_version

# Read from installed package metadata, NOT hand-maintained here. Same defect
# ccloop carried: this was hardcoded and had drifted two minor versions behind
# pyproject.toml (0.15.0 vs 0.17.0), so `ccmemory --version` reported a stale
# number when verifying an install. See ccloop/src/ccloop/__init__.py.
try:
    __version__ = _dist_version("ccmemory")
except PackageNotFoundError:
    __version__ = "0+unknown"

from .store import Store, Memory

__all__ = ["Store", "Memory", "__version__"]
