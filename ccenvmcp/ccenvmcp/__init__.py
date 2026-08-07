"""ccenvmcp — a tiny, stdlib-only, Python 3.9+ MCP server shim.

Drop-in for the slice of the official ``mcp`` SDK used by ccenv's tools-only
servers, without the SDK's Python >=3.10 floor.

    from ccenvmcp import FastMCP
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

# Read from metadata, not hand-maintained. Not yet drifted (this package has
# only ever been 0.1.0), but ccloop and ccmemory both drifted two minor
# versions the moment they were bumped twice — the defect is the second source
# of truth, not the number in it. importlib.metadata is stdlib from 3.8, so it
# holds under this package's 3.9 floor.
try:
    __version__ = _dist_version("ccenvmcp")
except PackageNotFoundError:
    __version__ = "0+unknown"

from .transport import FastMCP, build_input_schema

__all__ = ["FastMCP", "build_input_schema", "__version__"]
