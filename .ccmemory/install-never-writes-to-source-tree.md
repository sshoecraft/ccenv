---
name: install-never-writes-to-source-tree
description: No install.sh may write into the source tree — installs run as other users, or from someone else's git clone. Claude as steve can't see installs.
metadata:
  type: feedback
tags: [install, versioning, permissions, ccenv]
---

Never design a mechanism in which `install.sh` (ccenv's or any project's) writes into the source tree — for example a `RELEASED` marker next to `VERSION`. The user's answer when this was proposed: "NO NO AND NO! app user or NO user should be able 'write' to this dir!! ... remember some users git clone trader then ./install.sh".

Two facts that follow, and that any versioning or release-detection design has to accept:
- The user codes as `steve` and installs as the application user. `~/.config/ccenv/installed-version` sits in the installing user's home, so a Claude session running as `steve` cannot see what was installed or when.
- Other users install from their own `git clone`. An install is not an event this tree or this session ever observes.

So Claude cannot detect a release. Anything that has to happen "at release" has to happen before it instead. That is why version bumps occur once per session, before the first change ([[bump-top-level-bundle-version-not-just-subdir]]).
