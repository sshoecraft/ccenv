---
name: version-must-have-one-source-of-truth
description: Never hardcode __version__ in __init__.py — derive from importlib.metadata. ccloop and ccmemory both drifted 2 minor versions and lied during install…
metadata:
  type: feedback
tags: [versioning, ccloop, ccmemory, invariant, install]
---

## The failure

`__version__` was hardcoded in each package's `__init__.py` while
`pyproject.toml` carried the real number. Bumping a component meant bumping
pyproject; nobody bumped `__init__.py`. Both drifted two minor versions:

| | `--version` printed | installed dist |
|---|---|---|
| ccloop | 0.10.1 | 0.12.0 |
| ccmemory | 0.15.0 | 0.17.0 |

Caught only because someone ran `ccloop --version` to confirm an install had
taken. **A stale version reads as "the install failed"** — it sends you
debugging a deploy that already succeeded. That is the worst possible moment
for the number to be wrong.

## The rule

Never hand-maintain a version string in `__init__.py`:

```python
from importlib.metadata import PackageNotFoundError, version as _dist_version
try:
    __version__ = _dist_version("<pkg>")
except PackageNotFoundError:
    __version__ = "0+unknown"
```

`importlib.metadata` is stdlib from 3.8, so it holds even under ccenvmcp's 3.9
floor and adds no dependency.

Applied to ccloop, ccmemory AND ccenvmcp in v0.20.1. ccenvmcp had not drifted —
it was fixed anyway, because **the defect is the duplicated source of truth,
not the number currently in it.**

Consequence worth knowing: `--version` now reports what is *installed*, not
what is in the source tree. They differ until you reinstall. That is the
correct direction — see `ccenv-installed-vs-source-version`.

## The test that couldn't catch it

```python
from ccloop import __version__
assert __version__ in capsys.readouterr().out    # tautological
```

It imports the constant and asserts the command printed that same constant. It
passes at any drift. **When a test's expected value is derived from the same
thing it is testing, it verifies plumbing, not correctness.** Replaced with:
`__init__.py` must not hardcode a version (regex on the source), and
`__version__` must equal `importlib.metadata.version(pkg)`.

## Generalization

Same class as the ccmemory listing budget (estimator drifted from the
serializer it was supposed to model) and ccloop's handoff staleness (a
hand-maintained doc silently going stale). **Any value maintained by hand in a
second place will drift, and the drift is silent by construction.** When
auditing, the question is not "is this number right" but "how many places
carry this number".
