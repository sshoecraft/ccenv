---
name: feedback-stay-inside-this-repo
description: A ccenv session never edits, runs, tests or offers to touch another project's repo (mxfs etc.); report what that project should do and stop.
metadata:
  type: feedback
tags: [boundaries, cross-repo, ccproject]
---

A session working in /src/ccenv stays in /src/ccenv. It does not edit another project's files, run its tests or tools (even read-only), or offer to. The same holds the other way: an mxfs session that edited ccenv source and ran ccproject/install.sh was the problem that prompted this.

**Why:** the user, after an mxfs session changed ccenv's shared ccproject API and installed it unasked: "I don't want you to jack with another project just like I don't want another project to jack with this one." A ccenv session had just offered to run mxfs's parity test and was told no for the same reason.

**How to apply:**
- Reading another repo to *locate* how it uses ccenv tools is fine (grep -n for a call site). Changing it, executing anything in it, or offering to is not.
- When the fix belongs in another project, state what that project's session should do, as text for the user to carry over, and stop.
- Write proposals as proposals, not as numbered work lists: a list pasted into another project's session got executed wholesale, including the steps meant for this repo.
