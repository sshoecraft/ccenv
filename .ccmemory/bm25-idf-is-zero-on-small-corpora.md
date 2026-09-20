---
name: bm25-idf-is-zero-on-small-corpora
description: FTS5 bm25 scores a term in half the corpus at exactly 0, so 8-doc synthetic fixtures cannot test any similarity/clustering code.
metadata:
  type: project
tags: [trap, sqlite, fts5, testing, ccmemory]
---

SQLite FTS5 `bm25()` weights a term by rarity: IDF is `log((N - n + 0.5)/(n + 0.5))`, which is **exactly 0** when a term appears in half the documents and negative above that. Any test fixture small enough to hand-write has no discriminating vocabulary at all.

Measured while building `compile.compaction_plan` (ccmemory 0.19.0): a fixture of 4 notes about one subject and 4 about another, each with a distinct vocabulary, produced these scores for the seed's own query —

```
-0.9823  unit-note2     (the seed itself)
-0.9301  xfs-note2      (other subject, matched only the shared rare token "note2")
-0.0000  unit-note0     (same subject, every term shared -> IDF 0)
-0.0000  unit-note1
-0.0000  unit-note3
```

The same-subject notes scored zero and the cross-subject one scored nearly as well as the seed. The clustering code was correct; the fixture had no signal. Raising the corpus to 40 notes across 4 subjects (n/N = 0.25, IDF ≈ 1.07) made it behave as it does on the real store.

Two consequences that generalise beyond this module:

- **Never unit-test similarity, ranking or clustering on a toy corpus.** Size the fixture so each term appears in well under half of it, or the test asserts on noise. Assert on the real behaviour (coverage, disjointness, no-mixed-subjects) rather than on exact scores.
- **Avoid tokens that leak across fixture groups.** `xfs-note3` / `unit-note3` share the rare token `note3`, which BM25 rates highly precisely because it is rare. Give each synthetic note filler that is unique to it.

Related: FTS5 `bm25()` returns **negative** values, more negative = better match, and `ORDER BY rank ASC` yields best-first. Relative cutoffs therefore read backwards from the usual intuition — a candidate is worse than a floor when `hit > floor`.
