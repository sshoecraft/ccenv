---
name: feedback-changelog-names-no-other-project
description: ccenv's CHANGELOG must not mention mxfs or any other consuming project; describe the ccenv change in ccenv's own terms.
metadata:
  type: feedback
tags: [changelog, cross-repo]
---

ccenv's `CHANGELOG.md` records what changed in ccenv. It does not name mxfs (or any other project that uses ccenv) — not as the reason for a change, not as where something was measured, not for work done in that project.

**Why:** the user, on a v0.43.0 bullet describing mxfs's driver: "our changelog should not be mentioning mxfs." Work in another repo belongs in that repo's changelog; ccenv's entries should read correctly to someone who has never seen mxfs.

**How to apply:** when a change was driven or measured by another project, write the general case instead ("a project whose own tools write the same file", "on a store of 1,848 memories"). Check new entries with `grep -n -i mxfs CHANGELOG.md` before finishing.
