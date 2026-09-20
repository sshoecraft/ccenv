---
name: src-is-nfs-so-sqlite-wal-cannot-open
description: /src is an NFS mount: a WAL-mode SQLite file there cannot be opened at all, read or write. Check journal mode before blaming the code.
metadata:
  type: reference
tags: [nfs, sqlite, ccmemory, environment]
---

`/src` (and `/System/Volumes/Data/src`) is an NFS mount from 192.168.1.4, not
local disk:

    192.168.1.4:/src on /System/Volumes/Data/src (nfs, nodev, nosuid, automounted, noowners, nobrowse)

SQLite's WAL mode needs a shared `-shm` memory mapping. NFS does not provide
one, so a WAL-mode database file on that volume answers
`OperationalError: unable to open database file` to **every** statement from a
Mac client — including a read-only open. The file is intact: `open(path,'rb')`
reads `SQLite format 3` fine, and `?immutable=1` opens it and queries it. Only
the normal open path fails.

How to tell in one step, instead of chasing permissions:

    python3 -c "d=open(PATH,'rb').read(100); print('write_version', d[18])"   # 2 = WAL, 1 = rollback journal
    mount | grep /src

Two other symptoms that look like something else:

- `connect()` is lazy, so the traceback points at the first `PRAGMA`, not at
  the open. It reads like a pragma problem.
- Directory and file permissions are fine and a plain write to the directory
  succeeds, so every permission check comes back clean.

Anything storing SQLite under `/src` must therefore treat WAL as preferred, not
assumed, and fall back to `TRUNCATE`. `ccmemory`'s `Store._open` does this and
additionally rebuilds an index it cannot open — safe only because that index is
derived from the `.md` files. Do not copy the rebuild-on-failure half to a
database that holds anything original.

The AppleDouble `._*` sidecars that show up next to files there are the same
volume's doing — see [[ccenv-installed-vs-source-version]] for the other half
of the install-path confusion this volume causes.
