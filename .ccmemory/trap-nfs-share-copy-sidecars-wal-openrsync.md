---
name: trap-nfs-share-copy-sidecars-wal-openrsync
description: Moving data onto the 192.168.1.8 NFS shares (/data, /src): openrsync is 30x slow, every file gets a ._ sidecar, WAL SQLite can't open.
metadata:
  type: project
---

Three traps hit while moving ~130 GB of warrior research data from local disk to /data/warrior/store (NFSv3 from 192.168.1.8, gigabit). They apply to /src too (same server, same mount options).

1. **macOS `rsync` is openrsync and crawls on NFS**: 3.6 MB/s while a raw `dd` to the same share did 114 MB/s. A `tar -cf - . | tar -C dest -xf -` pipe ran 30–90 MB/s. Measure with `netstat -ibI en0` byte deltas around a timed `ping -c 11` (foreground sleep is blocked).
2. **Every file a Mac process creates on the share gets an AppleDouble `._<name>` sidecar**, because macOS stamps new files with the `com.apple.provenance` xattr and NFSv3 cannot store xattrs. `COPYFILE_DISABLE=1`, `tar --no-mac-metadata --no-xattrs`, and excluding `._*` do not stop it. `mount_nfs` has no option for NFSv3 (`nonamedattr` is NFSv4-only). 230,085 sidecars appeared; code that globs/listdirs a share directory took `._ACR_2026-09-30` as a fixture dir. Fix: delete them (`find … -name '._*' -delete`) AND make the consuming code skip `._*`.
3. **A WAL-mode SQLite file copied onto the share cannot be opened at all** — "unable to open database file", even `mode=ro` and `vfs=unix-none`. fcntl locks work and a fresh DELETE-mode db works; the failure is WAL's `-shm` mmap. Fix per db: connect, `pragma locking_mode=exclusive` BEFORE first access (SQLite then skips shared memory), `pragma journal_mode=delete`, `pragma quick_check`; then remove the stale `-shm`. 5,759 converted, 0 problems.

Also: verify copies by comparing file count + summed `stat -f %z` on both sides before deleting the source, and delete by `mv` into one holding folder then `rm -rf` that single full path (no variable/glob in rm).
