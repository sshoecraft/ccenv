"""ccmemory - persistent memory for Claude Code, file-of-truth + FTS5 index."""

__version__ = "0.8.0"

from .store import Store, Memory

__all__ = ["Store", "Memory", "__version__"]
