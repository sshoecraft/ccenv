---
name: feedback-ccenv-never-removes-a-package-that-left-it
description: ccenv's installer/uninstaller must never retire or remove a package that left ccenv to become its own project; find shim dependents by import, not by…
metadata:
  type: feedback
---

ccenv had listed a package that moved to its own repo as a "retired component": every `install.sh` run detected the user's separate install of it and ran the uninstaller on it, and a full `uninstall.sh` removed it too. The user had ccenv stop that and scrub every mention of the package.

**Why:** a package that left the bundle is the user's install of another project, not ccenv residue. Retirement is only for components ccenv shipped and dropped (ccprospect, ccinsight).

**How to apply:**
- Never add a moved-out package to `RETIRED_COMPONENTS`, `ALL_COMPONENTS`, or any removal list.
- Packages outside ccenv may import the `ccenvmcp` shim without declaring it (none of ccenv's own declare it either), so dependents are found by scanning top-level `--user` modules for `import ccenvmcp` — a fixed list would pull the shim out from under them.
- Before claiming two tools conflict, read the other tool's code, not a docstring about it: ccdirector's guard docstring claimed ccteam replays edits in a shared tree, which its shared-filesystem mode does not.
