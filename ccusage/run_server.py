#!/usr/bin/env python3
"""Entrypoint for the ccusage MCP server (stdio transport).

Run directly or via an MCP client config. Adds its own directory to sys.path
so it works regardless of the launching process's cwd.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import main

if __name__ == "__main__":
    main()
