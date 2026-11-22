"""Run the tests, and every subprocess they start (`python3 -m ccdirector.cli`),
against this source tree rather than an installed copy — ccdirector and the
ccenvmcp shim its MCP server is built on, which ship in the same bundle."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PATHS = [str(HERE / "src"), str(HERE.parent / "ccenvmcp")]
for entry in reversed(PATHS):
    sys.path.insert(0, entry)
os.environ["PYTHONPATH"] = os.pathsep.join(
    PATHS + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []))
