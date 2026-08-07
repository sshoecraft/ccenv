"""Version has exactly one source of truth: the package metadata.

`ccmemory --version` spent at least two releases printing 0.15.0 while the
installed distribution was 0.17.0, because `__init__.py` hardcoded the number
and pyproject.toml was the thing actually bumped. ccloop carried the identical
defect (0.10.1 vs 0.12.0). Both surfaced while verifying an install — which is
the worst possible moment for the version command to lie, since a stale number
reads as "the install did not take".

The fix was to delete the second source, not to remember to bump it. These
tests pin that property.
"""

import re
from pathlib import Path

import ccmemory


def test_version_is_not_hardcoded_in_the_package():
    src = Path(ccmemory.__file__).read_text(encoding="utf-8")
    assert "_dist_version" in src, "__version__ must come from package metadata"
    assert not re.search(r'^__version__\s*=\s*["\']', src, re.M), \
        "__version__ is hardcoded again — it will drift from pyproject.toml"


def test_version_matches_installed_distribution():
    from importlib.metadata import PackageNotFoundError, version

    try:
        assert ccmemory.__version__ == version("ccmemory")
    except PackageNotFoundError:
        # Source checkout with no installed dist — the honest fallback.
        assert ccmemory.__version__ == "0+unknown"
