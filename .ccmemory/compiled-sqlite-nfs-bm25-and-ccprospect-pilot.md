---
name: compiled-sqlite-nfs-bm25-and-ccprospect-pilot
description: SQLite traps (WAL on NFS /src, FTS5 bm25 IDF=0 on small fixtures) plus the ccprospect aitrader first live cycle results.
metadata:
  type: project
tags: [compiled, sqlite, nfs, fts5, ccprospect, ccmemory]
---

Group of loosely related notes: two SQLite/ccmemory environment traps and one ccprospect pilot record.

## WAL SQLite cannot open on NFS /src (`src-is-nfs-so-sqlite-wal-cannot-open`)
- `/src` (and `/System/Volumes/Data/src`) is an NFS mount from 192.168.1.4: `192.168.1.4:/src on /System/Volumes/Data/src (nfs, nodev, nosuid, automounted, noowners, nobrowse)`.
- WAL needs a shared `-shm` mapping, which NFS lacks, so a WAL-mode DB there answers `OperationalError: unable to open database file` to every statement from a Mac client, including read-only opens. File is intact: `open(path,'rb')` reads `SQLite format 3`; `?immutable=1` opens and queries it.
- One-step check: `python3 -c "d=open(PATH,'rb').read(100); print('write_version', d[18])"` (2 = WAL, 1 = rollback journal); `mount | grep /src`.
- Misleading symptoms: `connect()` is lazy so the traceback points at the first `PRAGMA`; permissions are fine and plain directory writes succeed.
- Anything storing SQLite under `/src` treats WAL as preferred, falls back to `TRUNCATE`. ccmemory `Store._open` does so and also rebuilds an index it cannot open; safe only because the index is derived from `.md` files. Do not copy rebuild-on-failure to a DB holding original data.
- AppleDouble `._*` sidecars there are the same volume's doing; see `ccenv-installed-vs-source-version`.

## FTS5 bm25 IDF is zero on small corpora (`bm25-idf-is-zero-on-small-corpora`)
- IDF = `log((N - n + 0.5)/(n + 0.5))` is exactly 0 when a term is in half the docs, negative above. Hand-written fixtures have no discriminating vocabulary.
- Measured building `compile.compaction_plan` (ccmemory 0.19.0): 4 notes on one subject, 4 on another, seed's own query scores: -0.9823 unit-note2 (seed), -0.9301 xfs-note2 (other subject, matched only shared rare token "note2"), -0.0000 unit-note0, unit-note1, unit-note3 (same subject, IDF 0). Clustering code was correct; fixture had no signal. 40 notes across 4 subjects (n/N = 0.25, IDF ~ 1.07) behaved like the real store.
- Never unit-test similarity/ranking/clustering on a toy corpus: each term in well under half of it; assert behaviour (coverage, disjointness, no-mixed-subjects), not exact scores. Avoid tokens leaking across groups (`xfs-note3`/`unit-note3` share rare `note3`); give each note unique filler.
- bm25() returns negative values, more negative = better; `ORDER BY rank ASC` is best-first; a candidate is worse than a floor when `hit > floor`.

## ccprospect first live cycle in aitrader, 2026-07-12 (`ccprospect-aitrader-first-cycle`)
- Agents itrader (opus) and atrader (gemma-4-31B) each completed one cycle with P1/P2 constitution steps live. Zero errors: MCP tools and SessionStart hook worked in agent sessions (no launcher MCP-pinning problem); reads on a missing store returned pending_count 0 without creating anything; store self-created in the run dir on first prospect_file.
- itrader: P1 journaled "PROSPECT INBOX (2A): prospect_inbox -> pending_count 0 (no store existed). Zero rows to disposition." P2 forced table with two rows: CREATE -> p-0001, and a reasoned NO_CONTRACT (forex re-entry: "nothing to re-check; fresh floorless survey every wake").
- p-0001: at-predicate 2026-07-13T13:25:00Z (09:25 ET, 5 min pre-open), expires 21:00Z. Intention = full playbook: $46.5k deployment, survey-first, ATR(14)-gated swing-low stops (>=1.5x ATR or size smaller/skip), prepared candidates with per-name stop/ATR geometry, correlation cluster rules, HARD BAN on sub-hour momentum chases. Evidence cites its own P&L audit (5/5 losers = sub-1x-ATR intraday stops). Friday-evening prep for Monday-open execution as an action-terminal artifact that survives overnight relays: the hesitation-tax counter the design targets.
- Store: `contracts/p-0001-*.md` + `events.jsonl` created event. PROSPECT.md absent because the store was created after the SessionStart hook ran; materializes at next relay/session start (not a bug).
- atrader (gemma): P1 "PROSPECTIVE INBOX: Pending count: 0." P2 one row: NONE | Market stagnant | N/A | N/A | NO_CONTRACT. Ritual-adjacent but honest and legal, GPT section 7.2's preferred failure mode (decline > fabricate). No store created. Track declined-then-moved stats if permanent.
- Watch: p-0001 fires at first evaluation >= 13:25Z Monday; discovery latency = wake cadence (agent plans its own pre-open wake; SessionStart hook re-surfaces the intention across relays). After the fire, the creation gate demands a disposition before new filings; expect ack done after deployment. Contract is intention-shaped (no expect/bucket) so the calibration record stays empty; if the owner wants it, ENCOURAGE (never require) expect+bucket in the constitution.
