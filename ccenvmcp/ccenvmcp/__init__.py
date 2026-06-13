"""ccenvmcp — a tiny, stdlib-only, Python 3.9+ MCP server shim.

Drop-in for the slice of the official ``mcp`` SDK used by ccenv's tools-only
servers, without the SDK's Python >=3.10 floor.

    from ccenvmcp import FastMCP
"""

__version__ = "0.1.0"

from .transport import FastMCP, build_input_schema

__all__ = ["FastMCP", "build_input_schema", "__version__"]
