---
name: compiled-sqlite-nfs-bm25-and-prospect-pilot
description: SQLite traps (WAL on NFS /src, FTS5 bm25 IDF zero on small corpora) plus the first live ccprospect aitrader pilot cycle results.
metadata:
  type: project
tags: [compiled, sqlite, nfs, fts5, ccmemory, ccprospect]
---

Shared theme: ccmemory/SQLite environment traps, plus one loosely related ccprospect pilot record.

## SQLite traps

### WAL on NFS [[src-is-nfs-so-sqlite-wal-cannot-open]]
`/src` (and `/System/Volumes/Data/src`) is an NFS mount from 192.168.1.4: `192.168.1.4:/src on /System/Volumes/Data/src (nfs, nodev, nosuid, automounted, noowners, nobrowse)`. WAL mode needs a shared `-shm` mapping, which NFS lacks, so a WAL-mode DB there answers `OperationalError: unable to open database file` to every statement from a Mac client, even read-only. File is intact: `open(path,'rb')` reads `SQLite format 3`; `?immutable=1` works.
Diagnose in one step: `python3 -c "d=open(PATH,'rb').read(100); print('write_version', d[18])"` (2 = WAL, 1 = rollback journal) and `mount | grep /src`.
Misleading symptoms: `connect()` is lazy so the traceback points at the first `PRAGMA`; directory/file permissions are fine and plain writes succeed.
Treat WAL as preferred, not assumed; fall back to `TRUNCATE`. `ccmemory` `Store._open` does this and rebuilds an index it cannot open, safe only because the index is derived from the `.md` files; do not copy rebuild-on-failure to a database holding original data. AppleDouble `._*` sidecars next to files there come from the same volume; see `ccenv-installed-vs-source-version`.

### bm25 IDF is zero on small corpora [[bm25-idf-is-zero-on-small-corpora]]
FTS5 `bm25()` IDF = `log((N - n + 0.5)/(n + 0.5))`, exactly 0 when a term is in half the documents, negative above. Measured building `compile.compaction_plan` (ccmemory 0.19.0), fixture of 4 notes on one subject and 4 on another:
```
-0.9823  unit-note2     (the seed itself)
-0.9301  xfs-note2      (other subject, matched only shared rare token "note2")
-0.0000  unit-note0     (same subject, every term shared -> IDF 0)
-0.0000  unit-note1
-0.0000  unit-note3
```
Clustering code was correct; fixture had no signal. 40 notes across 4 subjects (n/N = 0.25, IDF ~1.07) behaved like the real store.
- Never unit-test similarity/ranking/clustering on a toy corpus; keep each term well under half the corpus; assert behavior (coverage, disjointness, no-mixed-subjects), not exact scores.
- Avoid tokens leaking across fixture groups (`xfs-note3`/`unit-note3` share rare `note3`, scored highly because rare); give each note unique filler.
- `bm25()` returns negative values, more negative = better; `ORDER BY rank ASC` is best-first; a candidate is worse than a floor when `hit > floor`.

## ccprospect first live cycle in aitrader (2026-07-12) [[ccprospect-aitrader-first-cycle]]
Agents itrader=opus and atrader=gemma-4-31B completed one cycle with P1/P2 constitution steps live. Zero errors: MCP tools and SessionStart hook worked in agent sessions (no launcher MCP-pinning problem); reads on a missing store returned pending_count 0 without creating anything; store self-created in the run dir on first `prospect_file`.
- itrader (opus): P1 journaled "PROSPECT INBOX (2A): prospect_inbox -> pending_count 0 (no store existed). Zero rows to disposition." P2 table had TWO rows: CREATE -> p-0001, and a reasoned NO_CONTRACT (forex re-entry: "nothing to re-check; fresh floorless survey every wake"). p-0001: at-predicate 2026-07-13T13:25:00Z (09:25 ET, 5 min pre-open), expires 21:00Z; intention = full playbook: $46.5k deployment, survey-first, ATR(14)-gated swing-low stops (>=1.5x ATR or size smaller/skip), prepared candidates with per-name stop/ATR geometry, correlation cluster rules, HARD BAN on sub-hour momentum chases; evidence cites its own P&L audit (5/5 losers = sub-1x-ATR intraday stops). Friday-evening prep for Monday-open execution as an action-terminal artifact surviving overnight relays, the hesitation-tax counter the design targets. Store: `contracts/p-0001-*.md` + `events.jsonl` created event. PROSPECT.md absent because the store was created after the SessionStart hook ran; it appears at next relay/session start (not a bug).
- atrader (gemma): P1 "PROSPECTIVE INBOX: Pending count: 0." P2 one row: NONE | Market stagnant | N/A | N/A | NO_CONTRACT. Ritual-adjacent but honest and legal, GPT section 7.2's preferred failure mode (decline > fabricate). No store created (reads never create). Track declined-then-moved stats if permanent.
- Watch: p-0001 fires at first evaluation >= 13:25Z Monday; discovery latency = wake cadence (agent plans its own pre-open wake; SessionStart hook re-surfaces the intention across relays). After firing, the creation gate demands a disposition before new filings; expect ack done after deployment. Contract is intention-shaped (no expect/bucket) so the calibration record stays empty; if the owner wants calibration, ENCOURAGE (never require) expect+bucket in the constitution.
