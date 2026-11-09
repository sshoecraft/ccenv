"""Run the tests, and every subprocess they start (`python3 -m ccdirector.cli`),
against this source tree rather than an installed copy."""
import os
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")
sys.path.insert(0, SRC)
os.environ["PYTHONPATH"] = SRC + (os.pathsep + os.environ["PYTHONPATH"]
                                  if os.environ.get("PYTHONPATH") else "")
