"""Version has exactly one source of truth: the package metadata.

ccenvmcp had not drifted (it has only ever been 0.1.0), but ccloop and ccmemory
both drifted two minor versions the moment they were bumped more than once. The
defect is the second source of truth, not the number in it, so this package got
the same treatment before it could earn the bug.

importlib.metadata is stdlib from 3.8, so this holds under ccenvmcp's 3.9 floor
and keeps the package dependency-free.
"""

import re
from pathlib import Path

import ccenvmcp


def test_version_is_not_hardcoded_in_the_package():
    src = Path(ccenvmcp.__file__).read_text(encoding="utf-8")
    assert "_dist_version" in src, "__version__ must come from package metadata"
    assert not re.search(r'^__version__\s*=\s*["\']', src, re.M), \
        "__version__ is hardcoded again — it will drift from pyproject.toml"


def test_version_matches_installed_distribution():
    from importlib.metadata import PackageNotFoundError, version

    try:
        assert ccenvmcp.__version__ == version("ccenvmcp")
    except PackageNotFoundError:
        assert ccenvmcp.__version__ == "0+unknown"
