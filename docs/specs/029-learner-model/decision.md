# Decision log — Spec 029 (learner model bundle)

## Stage 0 spike findings (AGE cypher through asyncpg)

Three concrete divergences from the spec's assumed AGE behavior surfaced
while authoring `tests/test_age_spike.py`. The spec body was updated in
place; these notes capture the investigation path so the next contributor
doesn't re-run the same probes.

### 1. `create_graph(name)` is not idempotent

The spec originally suggested `SELECT create_graph('learner_knowledge')` at
migration time. That raises `duplicate key` on re-run. AGE 1.7 has no
`CREATE GRAPH IF NOT EXISTS` form.

**Resolution.** The migration guards with a `DO $$ … $$` block that checks
`ag_catalog.ag_graph` first:

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'learner_knowledge'
    ) THEN
        PERFORM create_graph('learner_knowledge');
    END IF;
END $$;
```

### 2. `ON CREATE SET` / `ON MATCH SET` on `MERGE` are not supported

openCypher's two-branch counter upsert pattern parses but errors with
`syntax error at or near "ON"`. The working equivalent is:

```cypher
MERGE (v:VocabItem {lemma: 'comer'})
SET v.production_count = coalesce(v.production_count, 0) + 1
```

The `SET` runs for both branches; `coalesce` covers the create-case NULL.
All counter upserts in `hable_ya/learner/graph.py` use this shape.

### 3. `count` is effectively reserved as a property name

`SET x.count = …` parses as an aggregate-function call — `SET r.count = 1`
fails with `syntax error at or near "="`. The collision is silent at
schema-design time (the relational `error_counts.count` column is fine —
SQL has no such clash), and turns up only when the cypher writer hits the
corresponding edge property.

**Resolution.** The `ErrorPattern` node and the `:MADE_ERROR` edge use
`occurrences` as the counter property instead of `count`. Names affected:
`(:ErrorPattern).occurrences`, `[:MADE_ERROR].occurrences`. The relational
column keeps its `count` name — no schema change needed there.

### 4. Tables were landing in `ag_catalog` instead of `public`

Spec 028 set `ALTER ROLE hable_ya SET search_path = ag_catalog, "$user", public`
so AGE's `create_graph` / `cypher(…)` resolve unqualified. Knock-on effect:
`CREATE TABLE learner_profile (…)` in the new migration put the table in
`ag_catalog`, which the session-scoped `db_pool` fixture noticed by way of
the pg_tables assertion.

**Resolution.** The migration runs `SET LOCAL search_path TO public, ag_catalog;`
at the top of `upgrade()` / `downgrade()`. Scoped to the transaction so the
role-level setting that AGE needs is unchanged.

### 5. String-literal escaping in cypher

Accents (`café`) and whitespace (`por favor`) pass through `$$…$$`-quoted
cypher bodies fine. Single quotes inside values break the cypher string
literal. Rather than escape, `hable_ya/learner/graph.py` rejects any
identifier that doesn't match `[\w\s.:/+-]+` at the repo layer and logs +
skips the write. The relational row still lands (aggregates stay coherent),
only the graph write is dropped.

---

## Other implementation-time decisions

### `get_session_theme` signature

The spec resolved Open Question #1 in favor of returning `Theme` rather than
`str`. The implementation also takes a `cooldown: int = 3` keyword so the
settings-driven `theme_cooldown` can override the default without callers
touching `recent_domains[-N:]` slicing.

### `content_tokens` exposed from `eval.scoring.recast`

Renamed `_content_tokens` → `content_tokens` so
`hable_ya.learner.vocabulary` can share the POS filter. The vocabulary
module still re-walks the spaCy doc (to capture the surface form alongside
the lemma) rather than consuming `content_tokens` verbatim; the rename is
what makes the shared module contract visible, not the duplication.

### `spacy` moved from `eval` extras to main dependencies

Runtime vocabulary tracking is now production code. `spacy>=3.7` is a
top-level dependency; `es_core_news_sm` remains a setup step (not in
uv.lock by design — spaCy models are pulled via `python -m spacy download`).
`VocabularyRepo.record` catches `ImportError` / `OSError` at call time and
returns `[]` so a missing model doesn't crash the ingest transaction.

### Ingestion: double-write in one transaction

`TurnIngestService.ingest` opens a single `conn.transaction()` and composes:
`INSERT turns` → `ErrorRepo.record` → `VocabularyRepo.record` → graph
upserts. Failures roll the whole turn back; `TurnObservationSink.ingest_failed`
is incremented by the caller (`HableYaToolHandler`) so the researcher can
watch drift between JSONL and DB via `/dev/observations`.

### Session wiring: `build_session_prompt` returns metadata

`build_system_prompt` kept its `str` return so the byte-identity tests stay
succinct. The new `build_session_prompt` returns a `SessionPrompt`
dataclass (`text`, `theme`, `band`) so the WS handler can pass
`theme.domain` into `TurnIngestService.start_session` without re-running
the theme pick.

### Test-harness note: httpx over TestClient

`tests/test_dev_endpoints.py` uses `httpx.AsyncClient` + `ASGITransport`
rather than FastAPI's `TestClient`. The sync TestClient runs handlers on a
thread with its own event loop; that can't share the session-scoped
`db_pool` without tripping asyncpg's "another operation is in progress"
guard.

---

## Open items deferred to follow-up specs

- **Manual 5-minute Spanish conversation.** The plan's Stage 8 lists a
  live-session smoke test (psql spot-check, `ingest_failed` reaction to
  `docker compose stop db`, AGE edge-count inspection). Deferred to a
  dedicated manual-validation pass before PR merge; requires
  CUDA / llama.cpp / audio I/O that automated tests don't exercise.
- **Decision on purging the Stage 0 spike file.** `tests/test_age_spike.py`
  still lives in the tree. It's cheap to keep (all six tests pass; they
  exercise edge cases the production graph writer doesn't) but the spec
  plan flagged it as throwaway. Keeping until PR review.
