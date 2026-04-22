# Spec: Learner Model — Profile, Error Patterns, Vocabulary, Themes, AGE Knowledge Graph

| Field | Value |
|---|---|
| id | 029 |
| status | draft |
| created | 2026-04-22 |
| covers roadmap | #029, #030, #031, #032, #033 (bundle) |

---

## Why

Spec 023 wired the agent loop end-to-end — the model emits structured `log_turn` observations on ~80% of turns and they land in `runtime_turns.jsonl` plus an in-memory ring buffer. Spec 028 stood up Postgres + Apache AGE so durable storage exists. The two are not connected yet: every observation is thrown away on app restart, the prompt builder feeds the model neutral midpoint values for L1 reliance / fluency / error patterns / vocabulary regardless of who's actually been talking, and `THEMES_BY_LEVEL` is an empty dict with `get_session_theme()` raising `NotImplementedError`. The result: the agent cannot adapt to the learner's actual error history or vocabulary growth, every session feels generic, and the topic of conversation is whatever the learner happens to bring up (or the static "conversación abierta" fallback).

This slice closes the loop. It defines the persistent learner model — relational tables for scalar aggregates (current band, session counts, error counts per category, vocabulary item counts) + an Apache AGE knowledge graph for the relationships between concepts, errors, and vocabulary items — and connects three open ends:

1. **`log_turn` ingestion path.** Every observation that today flows into the JSONL sink also writes through to the relational tables and the AGE graph. The JSONL sink stays as a dev artifact for inspection but is no longer the system of record.
2. **Prompt-builder reads from the profile.** `build_system_prompt` pulls the learner's actual production level, L1 reliance, fluency signal, top error patterns, and vocab strengths from the profile rather than band midpoints. The agent the learner hears in their fifth session knows what they got wrong in their fourth.
3. **Theme rotation per band.** `THEMES_BY_LEVEL` is populated with hand-authored Spanish scenarios per CEFR band (A1–C1, ~10 each); `get_session_theme` picks one excluding the last 3 used (cooldown), so consecutive sessions feel varied instead of repeating the same conversational frame.

### Consumer Impact

- **End user (learner):** The agent's Spanish becomes a function of *this* learner, not a generic A2-band learner. Repeated error categories surface in the prompt as "known error patterns" so the model can choose recasts that re-target them; vocabulary the learner has produced shows up as "vocabulary strengths" so the model doesn't reintroduce basics; sessions cycle through different scenarios (ordering coffee → talking about a recent trip → giving advice about a problem at work) instead of always defaulting to "conversación abierta". Across a handful of sessions the experience moves from "generic conversation partner" toward "tutor who remembers".
- **Project owner (researcher/developer):** Has a queryable system of record. Can run `psql` queries to see per-category error counts over time, vocabulary growth curves, and (via AGE cypher) the structural learner-knowledge graph that #033 was originally about. The dev endpoint surfaces the live profile alongside the recent observations so the relationship between a `log_turn` and the profile update it produced is inspectable in one place. Auto-promotion (#049) and placement calibration (#050) now have a concrete data substrate to consume.
- **Downstream features:** #034–#036 (agent eval) can stand up synthetic-learner sessions against a real learner profile rather than mocked state. #042 (artifact registry) gets a place to attach to. #049 and #050 (the deferred auto-level and calibration features) read aggregate signals from the profile.

### Roadmap Fit

Bundles five planned items:

- **#029** Learner profile module (state across sessions).
- **#030** Learner error-pattern tracking.
- **#031** Learner vocabulary tracking.
- **#032** Theme selection (`THEMES_BY_LEVEL` + `get_session_theme()`).
- **#033** Knowledge-graph learner model in Apache AGE (node/edge schema for skills, concepts, errors, progression).

Bundling rationale: the five items are deeply coupled. The profile owns the band that themes are selected against and that the prompt is rendered for; errors and vocabulary are both written from the same `log_turn` observation and both are consumed by the prompt; the AGE graph holds the relational structure (co-occurrence, prerequisites, progression) that links errors to concepts to vocabulary. Splitting them produces four PRs whose intermediate states are non-functional (a profile with no ingestion source, errors with nowhere to surface, vocabulary with no schema). Spec 023 set the precedent for bundling tightly-coupled roadmap items.

Explicitly **not** bundled here:

- **#049 (auto-level)** and **#050 (placement calibration)** — deferred. This spec keeps the band static (manual config) but exposes the aggregate signals (error density per category, vocabulary growth curve, recent fluency signal trend) those specs will consume. See ROADMAP.md 2026-04-22 entry.
- **#034–#036 (agent eval)** — independent; they consume what this spec produces.

Dependencies:

- Upstream (required): #023 (agent loop / `log_turn` handler), #028 (Postgres + AGE plumbing). Both implemented.
- Downstream (unblocked by this): #034–#036, #042, #049, #050.

---

## What

### Acceptance Criteria

With llama.cpp + Postgres + `api/main.py` + `web/` running:

- [ ] `THEMES_BY_LEVEL` is populated with ≥ 8 hand-authored Spanish scenarios per CEFR band (A1, A2, B1, B2, C1) — total ~50. Each scenario is a `Theme` instance with `domain`, `prompt`, and `target_structures` populated to a quality bar comparable to the existing `_NEUTRAL_THEME` in `hable_ya/pipeline/prompts/builder.py`.
- [ ] `get_session_theme(*, level: CEFRBand, recent_domains: list[str]) -> Theme` returns a deterministic `Theme` chosen from `THEMES_BY_LEVEL[level]` whose `domain` is not in the last 3 entries of `recent_domains`. The signature changes from the current stub (string return) to `Theme` return; rationale in Key Decisions.
- [ ] On a fresh database, the first WS session opens with profile defaults (band = `settings.default_learner_band`, sessions_completed = 0, no error history, no vocabulary). The prompt builder uses these defaults and renders without divergence from the spec-023 byte-identity tests (which continue to pass for the cold-start case).
- [ ] After the first turn of session 1, the agent's `log_turn` observation has been:
    1. Written to `runtime_turns.jsonl` as today (sink behavior preserved).
    2. Persisted as a row in `turns`.
    3. Aggregated into `error_counts` (one row incremented per `error.type` in the observation).
    4. Aggregated into `vocabulary_items` (one row upserted per content-word lemma in the learner utterance, with `last_seen_at` updated and `production_count` incremented).
    5. Reflected in the AGE graph: `(:Learner)-[:PRODUCED]->(:VocabItem {lemma: ...})` edges added/updated, `(:Learner)-[:MADE_ERROR]->(:ErrorPattern {category: ...})` edges added/updated with a `count` property.
- [ ] On the second session, `build_system_prompt` reads the persisted profile and renders a prompt where: `production_level` reflects the band's current static value, `L1_reliance` is the rolling mean of the last N (default 20) `L1_used` booleans, `speech_fluency` is the numeric mapping of the rolling-mean fluency signal, `error_patterns` lists the top 3 most-counted `error_counts.category` rows, and `vocab_strengths` lists the top 5 most-recently-used vocabulary lemmas. None of these come from band midpoints anymore.
- [ ] On the second session, the system prompt's `## Topic:` block is no longer the neutral "conversación abierta" theme — it is a `Theme` returned by `get_session_theme(level=profile.band, recent_domains=[…last 3 from sessions])`. The neutral theme remains the fallback if `THEMES_BY_LEVEL[band]` has been exhausted by cooldown (impossible at ≥ 4 scenarios per band, but safety belt nonetheless).
- [ ] The profile, error_counts, vocabulary_items, sessions, and turns tables exist and are populated by an alembic revision (or revisions) authored under `hable_ya/db/alembic/versions/`. The revision uses raw `op.execute("...")` SQL per the spec-028 convention, no SQLAlchemy ORM models.
- [ ] An AGE graph named `learner_knowledge` exists in Postgres after migrations, with documented node labels (`:Learner`, `:VocabItem`, `:ErrorPattern`, `:Concept`, `:Scenario`) and edge labels (`:PRODUCED`, `:MADE_ERROR`, `:ENGAGED_WITH`, `:RELATES_TO`). The graph is created idempotently in a migration (uses `CREATE GRAPH IF NOT EXISTS` semantics — see Approach for the exact form).
- [ ] `GET /dev/observations` is extended (or a sibling `GET /dev/learner` is added — see Open Questions) to return the current profile snapshot alongside the recent observations, so the project owner can correlate a turn with the profile update it produced.
- [ ] `tests/test_learner_profile.py`, `tests/test_learner_errors.py`, `tests/test_learner_vocabulary.py`, `tests/test_themes.py`, and `tests/test_learner_graph.py` exist and pass against the live test Postgres (using the existing `db_pool` session-scoped fixture). Each covers the read + write path for its module against a known fixture observation.
- [ ] A new integration test (`tests/test_log_turn_ingestion.py`) feeds a `TurnObservation` end-to-end through the new ingestion service and asserts: a `turns` row was inserted, the right `error_counts` rows were incremented, the right `vocabulary_items` were upserted, and the AGE graph contains the expected nodes + edges.
- [ ] The full pytest suite passes (DB tests run when Postgres is reachable, skip cleanly when not — preserving the spec-028 skip behavior).
- [ ] `ruff` and `mypy` pass on the new and modified files. The CI scope (`hable_ya/`, `api/`, the listed test files) is extended to include the new `tests/test_learner_*.py` files.

### Non-Goals

- **No auto-promotion / auto-demotion.** The band stays whatever `settings.default_learner_band` says (or a manual override). The profile *exposes* the signals (error density per category, vocabulary growth curve, recent fluency signal mean) that #049 will eventually consume, but does not act on them. See ROADMAP.md #049.
- **No placement calibration.** New deployments boot at `settings.default_learner_band`, full stop. No diagnostic flow infers an initial level. See ROADMAP.md #050.
- **No multi-tenancy.** One `Learner` node, one profile row, one set of aggregates per deployment. No `learner_id` column anywhere. (Per project memory: single-tenant only.)
- **No schema migration of existing JSONL data.** Any `runtime_turns.jsonl` content from previous sessions is not back-filled into the new tables. The cutover is forward-only; the file keeps appending as before.
- **No retraining loop.** This spec writes to the database. It does not produce SFT training examples from real-session data, and it does not feed observations back into a fine-tune. That is a separate roadmap item (not currently planned).
- **No frontend surface.** No live profile widget on the orb screen, no progression bar, no error-pattern badge. The project owner inspects via the dev endpoint and `psql`. A frontend slice would be a separate spec.
- **No graph-driven prompt content.** The prompt continues to consume scalar profile fields (band, L1_reliance, fluency, top errors, top vocab). The AGE graph stores the relational structure for future use (recommendation engine, prerequisite-aware theme selection, concept gap analysis) but is not read by the prompt builder in this slice. See Key Decisions.
- **No observation deletion / GDPR flow.** Single-tenant, on-device — the user owns the database. No "forget me" path beyond `DROP DATABASE`.
- **No second tool.** `HABLE_YA_TOOLS` continues to define only `log_turn`. Per fine-tune scope memory, that's the only tool the model is trained to emit.
- **No theme-prerequisite logic.** Themes are chosen by random rotation within band, with cooldown. They are not selected based on the learner's vocabulary gaps, error history, or graph state. (A future feature that does this would consume the AGE graph; this spec lays the substrate but does not build the recommender.)

### Open Questions

1. ~~**`get_session_theme` return type — `str` (domain identifier) vs `Theme` (full object)?**~~ **Resolved: return `Theme`.** The only consumer (`build_system_prompt` via `render_system_prompt`) needs a full `Theme`; returning `str` would force the builder to maintain a parallel `domain → Theme` lookup, defeating the point of `THEMES_BY_LEVEL`. Cooldown parameter renames from `recent_themes: list[str]` to `recent_domains: list[str]` for clarity.

2. ~~**Aggregate-table sync strategy: double-write or graph-as-source-of-truth?**~~ **Resolved: double-write in one transaction.** Aggregates are tiny (one row per error category, one per vocab lemma); write cost is negligible; read path stays a simple `SELECT` for the prompt builder. Materialized views add operational complexity (refresh triggers, staleness windows) for no win at single-tenant scale.

3. ~~**Vocabulary lemmatization: spaCy at write time, or store raw tokens?**~~ **Resolved: lemmatize at write time; store the lemma + a sample raw form for inspection.** spaCy's `es_core_news_sm` is already a project dependency; ~1ms per call; produces a clean primary key on `vocabulary_items.lemma` so reads stay simple.

4. ~~**Top-N error patterns / vocab strengths in the prompt — fixed N or threshold-based?**~~ **Resolved: fixed N. Top 3 errors by count over the last K=20 turns, top 5 vocab lemmas by recency over the same window.** Tunable via settings (`profile_top_errors`, `profile_top_vocab`, `profile_window_turns`); defaults align with what the prompt template can absorb without dominating.

5. ~~**Dev endpoint shape: extend `/dev/observations` or add `/dev/learner`?**~~ **Resolved: add `GET /dev/learner` for the profile snapshot + top error/vocab tables; leave `/dev/observations` unchanged.** Symmetric with the modules they correspond to.

6. ~~**Rolling-window storage: derive on read, or store a derived column?**~~ **Resolved: derive on read.** Profile reads happen once per session start, not per turn; the SELECT is over at most 20 rows. Avoids a precomputed column that drifts if any historical row is touched.

---

## How

### Approach

Five concerns to wire, in implementation order:

#### 1. Database schema (alembic revision)

A single new alembic revision under `hable_ya/db/alembic/versions/<rev>_learner_model.py` creates:

**Relational tables** (raw `op.execute("...")` SQL, per spec-028):

```sql
CREATE TABLE learner_profile (
    id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- single row, single tenant
    band            TEXT NOT NULL CHECK (band IN ('A1','A2','B1','B2','C1')),
    sessions_completed INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    session_id      TEXT PRIMARY KEY,                -- 12-char hex from session.py
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    theme_domain    TEXT,                            -- the domain chosen by get_session_theme
    band_at_start   TEXT NOT NULL CHECK (band_at_start IN ('A1','A2','B1','B2','C1'))
);

CREATE TABLE turns (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    timestamp       TIMESTAMPTZ NOT NULL,
    learner_utterance TEXT NOT NULL,
    fluency_signal  TEXT NOT NULL CHECK (fluency_signal IN ('weak','moderate','strong')),
    L1_used         BOOLEAN NOT NULL,
    raw_extra       JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX turns_session_idx ON turns(session_id);
CREATE INDEX turns_timestamp_idx ON turns(timestamp DESC);

CREATE TABLE error_observations (
    id              BIGSERIAL PRIMARY KEY,
    turn_id         BIGINT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,    -- e.g. "ser_estar"
    produced_form   TEXT NOT NULL,
    target_form     TEXT NOT NULL
);
CREATE INDEX error_observations_category_idx ON error_observations(category);

CREATE TABLE error_counts (
    category        TEXT PRIMARY KEY,
    count           INT NOT NULL DEFAULT 0,
    last_seen_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE vocabulary_items (
    lemma           TEXT PRIMARY KEY,
    sample_form     TEXT NOT NULL,    -- one observed surface form for human inspection
    production_count INT NOT NULL DEFAULT 0,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX vocabulary_items_last_seen_idx ON vocabulary_items(last_seen_at DESC);

INSERT INTO learner_profile (id, band) VALUES (1, 'A2');  -- bootstrapped; band updated by app on first start
```

**AGE graph** (separate `op.execute(...)` block in the same revision):

```sql
-- `create_graph(name)` is NOT idempotent (raises if the graph exists), per
-- the Stage 0 spike. The migration guards by checking ag_catalog.ag_graph
-- first so re-runs succeed:
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'learner_knowledge'
    ) THEN
        PERFORM create_graph('learner_knowledge');
    END IF;
END $$;
```

The graph contains five node labels and four edge labels, all created lazily by the application's first cypher writes (AGE creates labels on first use). Documented in the spec but not pre-created in the migration:

- `(:Learner {id: 1})` — the single learner node, created at app startup.
- `(:VocabItem {lemma, first_seen_at, last_seen_at})` — one per distinct lemma the learner has produced.
- `(:ErrorPattern {category, count})` — one per distinct error category.
- `(:Concept {name})` — currently unused by the prompt; populated lazily as a future hook for prerequisite-aware theme selection.
- `(:Scenario {domain, band})` — one per `THEMES_BY_LEVEL` entry; populated at app startup from the constant.

Edges:

- `(:Learner)-[:PRODUCED {at}]->(:VocabItem)` — written on every turn for each lemma in the utterance.
- `(:Learner)-[:MADE_ERROR {at}]->(:ErrorPattern)` — written on every error in a turn.
- `(:Learner)-[:ENGAGED_WITH {at}]->(:Scenario)` — written once per session at the start.
- `(:VocabItem)-[:RELATES_TO]->(:Concept)` — currently empty; future hook.

The migration also runs the spec-028-pattern role-level setting if needed (no-op — already done in the prior revision; included here only if a fresh schema requires it).

#### 2. Theme content + selection (`hable_ya/learner/themes.py`)

Replace the current stub with a full `THEMES_BY_LEVEL: dict[CEFRBand, list[Theme]]` populated with hand-authored scenarios. Below is the authored list (10 per band, total 50). All scenarios use the existing `eval.fixtures.schema.Theme` model (`domain`, `prompt`, `target_structures`).

**A1 (10):** `presentarse`, `pedir_un_cafe`, `hablar_de_la_familia`, `los_numeros_y_la_hora`, `comprar_en_el_mercado`, `pedir_direcciones`, `hablar_del_clima`, `mi_dia_normal`, `comida_favorita`, `colores_y_ropa`.

**A2 (10):** `planes_para_el_fin_de_semana`, `describir_tu_ciudad`, `en_el_restaurante`, `viajar_en_tren_o_autobus`, `ir_al_medico`, `vacaciones_pasadas`, `llamar_por_telefono`, `alquilar_un_piso`, `contar_una_rutina_diaria`, `hablar_del_trabajo_actual`.

**B1 (10):** `experiencia_inolvidable`, `problema_en_el_trabajo`, `tu_vida_hace_diez_anos`, `planear_un_viaje_con_amigos`, `consejos_sobre_salud`, `pelicula_o_libro_que_te_marco`, `problemas_con_un_electrodomestico`, `buscar_trabajo`, `redes_sociales_en_tu_vida`, `un_malentendido_reciente`.

**B2 (10):** `impacto_del_teletrabajo`, `decision_dificil`, `debate_dietas_y_salud`, `tecnologia_y_privacidad`, `conflicto_entre_amigos_o_familia`, `viajar_por_trabajo_vs_placer`, `proyecto_que_lideraste`, `costumbres_de_otra_cultura`, `cambios_en_tu_ciudad`, `educacion_de_hoy`.

**C1 (10):** `ia_en_la_sociedad`, `crisis_climatica_y_accion_individual`, `arte_como_protesta`, `futuro_del_trabajo_humano`, `tradiciones_que_deberian_desaparecer`, `papel_de_los_medios`, `dilema_etico_reciente`, `salud_mental_en_la_sociedad`, `identidad_cultural_globalizada`, `libro_o_ensayo_transformador`.

Each `Theme` entry is authored with:

- `domain`: short human-readable Spanish identifier (e.g. `"pedir un café"`).
- `prompt`: 1–2 Spanish sentences telling the model what scenario to drive (e.g. `"El estudiante quiere pedir un café en una cafetería. Tú eres el camarero — empieza saludando y preguntando qué desea."`).
- `target_structures`: list of grammatical structures appropriate to the band that the scenario naturally elicits (e.g. `["querer + infinitivo", "saludos formales"]`). May be empty if the scenario is open-ended.

The full content is authored inline in `hable_ya/learner/themes.py` during implementation; the spec lists the domain identifiers above so the count and shape are committed.

```python
def get_session_theme(*, level: CEFRBand, recent_domains: list[str]) -> Theme:
    pool = THEMES_BY_LEVEL[level]
    cooldown = set(recent_domains[-3:])
    candidates = [t for t in pool if t.domain not in cooldown]
    if not candidates:
        # Pool exhausted by cooldown — falls back to neutral theme rather
        # than picking from cooldown. Requires THEMES_BY_LEVEL[level] to
        # have ≥4 entries, which the authored content satisfies.
        return _NEUTRAL_THEME  # imported from builder.py or relocated here
    return random.choice(candidates)
```

`random.choice` is acceptable — selection is non-deterministic by design (variety across sessions) and reproducibility is not a goal here.

#### 3. Profile / errors / vocabulary modules (`hable_ya/learner/`)

Replace the four empty docstring files with concrete implementations:

- **`hable_ya/learner/profile.py`** — `class LearnerProfileRepo`:
    - `async def get(pool) -> LearnerProfileSnapshot` — reads the single profile row + computes rolling-window aggregates from `turns` over the last K=20 rows (L1_reliance mean, fluency_signal mean mapped to a float in `[0, 1]`), top 3 error categories from `error_counts` (by count desc), top 5 vocab lemmas from `vocabulary_items` (by `last_seen_at` desc). Returns a `LearnerProfileSnapshot` dataclass shaped to match the existing `LearnerProfile` Pydantic model in `eval/fixtures/schema.py` so the prompt builder can drop it in unchanged.
    - `async def increment_session_count(pool) -> None` — `UPDATE learner_profile SET sessions_completed = sessions_completed + 1, updated_at = now() WHERE id = 1`.
    - `async def set_band(pool, band: CEFRBand) -> None` — manual override hook for #049 to consume later; not called in this slice (band is read from settings).

- **`hable_ya/learner/errors.py`** — `class ErrorRepo`:
    - `async def record(pool, turn_id: int, errors: list[dict[str, str]], at: datetime) -> None` — inserts one row per error into `error_observations`, then `INSERT … ON CONFLICT (category) DO UPDATE SET count = count + 1, last_seen_at = excluded.last_seen_at` per category in `error_counts`.
    - `async def top_categories(pool, limit: int = 3) -> list[str]` — `SELECT category FROM error_counts ORDER BY count DESC, last_seen_at DESC LIMIT $1`.

- **`hable_ya/learner/vocabulary.py`** — `class VocabularyRepo`:
    - `async def record(pool, utterance: str, at: datetime) -> list[str]` — runs spaCy `es_core_news_sm` over `utterance`, extracts content-word lemmas (POS in `{NOUN, VERB, ADJ, ADV}`, ignore stopwords), upserts each into `vocabulary_items` (`production_count + 1`, `last_seen_at = excluded.last_seen_at`, `first_seen_at` set on insert only). Returns the list of lemmas observed (used by the graph writer).
    - `async def top_recent(pool, limit: int = 5) -> list[str]` — `SELECT lemma FROM vocabulary_items ORDER BY last_seen_at DESC LIMIT $1`.

- **`hable_ya/learner/themes.py`** — already covered in §2 above.

#### 4. Knowledge-graph writer (`hable_ya/learner/graph.py` — new file)

A small wrapper over asyncpg + AGE cypher that handles the four edge writes. Per spec-028, AGE multi-arg functions need explicit type casts (`drop_graph('name'::name, true)`); cypher calls don't have that issue but caller-side parameter passing through `cypher(...)` requires careful quoting since AGE cypher's `$param` syntax doesn't match asyncpg's. The implementation uses string-formatted cypher with sanitized values (lemma / category strings are single-token alphanumeric in practice; non-conforming inputs are rejected at the application layer before reaching cypher).

```python
async def upsert_vocab(pool, lemma: str, at: datetime) -> None
async def upsert_error_pattern(pool, category: str, at: datetime) -> None
async def link_session_to_scenario(pool, session_id: str, scenario_domain: str, at: datetime) -> None
async def ensure_learner_node(pool) -> None  # idempotent, called at app startup
async def ensure_scenario_nodes(pool) -> None  # idempotent, called at app startup
```

Each function does a single `MERGE` on the target node + node properties, then `MERGE`s the edge with an `at` timestamp property. `MERGE` is AGE-supported and gives the upsert semantics we want.

**AGE MERGE caveat (Stage 0 spike finding):** AGE does not support the openCypher `ON CREATE SET … ON MATCH SET …` clauses (parser errors with `syntax error at or near "ON"`). Counter upserts use the coalesce form instead:

```cypher
MERGE (v:VocabItem {lemma: 'comer'})
SET v.production_count = coalesce(v.production_count, 0) + 1
```

The `SET` runs for both branches; `coalesce` handles the create-case NULL. Edge upserts use the same pattern for their counter / `last_at` properties. Accents and internal whitespace inside `'…'` literals are handled correctly when the cypher body is wrapped in `$$…$$` (dollar-quoted). Single quotes inside values are not — the repo layer rejects non-`[\w\s.:/+-]+` identifiers before they reach cypher rather than attempting SQL-style escaping.

**Second AGE caveat:** the property name `count` is effectively reserved — `SET x.count = …` fails with `syntax error at or near "="` on both nodes and edges, because the parser treats `count` as the aggregate function. The error-pattern node + edge use `occurrences` instead; the relational `error_counts.count` column is unaffected (SQL does not have the same collision).

#### 5. Ingestion service (`hable_ya/learner/ingest.py` — new file)

The single chokepoint that replaces the JSONL-only sink dispatch. Called by the rewritten `HableYaToolHandler` after `normalize_runtime_log_turn_args` succeeds:

```python
class TurnIngestService:
    def __init__(self, pool: asyncpg.Pool) -> None: ...

    async def ingest(self, obs: TurnObservation) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                turn_id = await self._insert_turn(conn, obs)
                if obs.errors:
                    await self._record_errors(conn, turn_id, obs.errors, obs.timestamp_iso)
                lemmas = await self._record_vocabulary(conn, obs.learner_utterance, obs.timestamp_iso)
                # AGE writes share the same transaction
                for category in {e["type"] for e in obs.errors}:
                    await graph.upsert_error_pattern(conn, category, obs.timestamp_iso)
                for lemma in lemmas:
                    await graph.upsert_vocab(conn, lemma, obs.timestamp_iso)

    async def start_session(self, session_id: str, theme_domain: str, band: CEFRBand) -> None:
        # INSERT into sessions, increment sessions_completed, link learner→scenario in graph.
        ...

    async def end_session(self, session_id: str) -> None:
        # UPDATE sessions SET ended_at = now() WHERE session_id = $1.
        ...
```

The service is constructed once at app startup (in the FastAPI lifespan) using `app.state.db_pool` and attached to `app.state.ingest`. The `HableYaToolHandler` constructor signature gains a `ingest: TurnIngestService` parameter; `build_pipeline` and `build_pipeline_task` thread it through from `app.state`. The JSONL `TurnObservationSink.append` continues to be called for backward compatibility with `/dev/observations`; on top of that, the handler now also calls `ingest.ingest(obs)`.

If the database write fails, the handler logs an error, increments a new `ingest_failed` counter on the sink, and proceeds — the JSONL write has already happened, so the observation is not lost. The pipeline continues uninterrupted (graceful degradation, mirroring the existing `missing` counter pattern from spec 023). This is the same posture as `log_turn` parsing failures: log + count + don't crash.

#### 6. Prompt builder integration (`hable_ya/pipeline/prompts/builder.py`)

`build_system_prompt` becomes async (its only caller, `session_ws`, is already async) and gains `pool: asyncpg.Pool` + `recent_domains: list[str]` parameters. The function:

1. Calls `LearnerProfileRepo.get(pool)` to get the snapshot.
2. Builds a `LearnerProfile` from the snapshot (drop-in for the existing midpoint-based one).
3. Calls `get_session_theme(level=snapshot.band, recent_domains=recent_domains)` to get a `Theme`.
4. Calls `render_system_prompt(SystemParams(profile=..., theme=...), band=snapshot.band)`.
5. Optionally appends `COLD_START_INSTRUCTIONS` if `snapshot.sessions_completed == 0`.

The cold-start case (no profile data) returns the same byte-identical prompt that today's tests check, by virtue of the snapshot having neutral defaults when no `turns` exist. The spec-023 byte-identity tests stay green.

`session_ws` queries `recent_domains` from the `sessions` table (last 3 `theme_domain` values ordered by `started_at` desc) and passes them in.

#### 7. Settings additions

```python
# hable_ya/config.py
profile_window_turns: int = 20            # rolling window for L1_reliance / fluency mean
profile_top_errors: int = 3               # top-N error categories surfaced in prompt
profile_top_vocab: int = 5                # top-N vocab lemmas surfaced in prompt
theme_cooldown: int = 3                   # number of recent themes excluded from selection
```

All four are tunable via env (`HABLE_YA_PROFILE_WINDOW_TURNS`, etc.) with sensible defaults.

#### 8. Module ownership recap

- `hable_ya/learner/profile.py` — `LearnerProfileRepo`, `LearnerProfileSnapshot`.
- `hable_ya/learner/errors.py` — `ErrorRepo`.
- `hable_ya/learner/vocabulary.py` — `VocabularyRepo`.
- `hable_ya/learner/themes.py` — `THEMES_BY_LEVEL` (full content), `get_session_theme`, `_NEUTRAL_THEME` (relocated from builder.py).
- `hable_ya/learner/graph.py` — AGE cypher writers.
- `hable_ya/learner/ingest.py` — `TurnIngestService`.
- `hable_ya/pipeline/prompts/builder.py` — async; reads from profile + themes.
- `hable_ya/pipeline/processors/tool_handler.py` — gains `ingest` constructor arg.
- `hable_ya/pipeline/runner.py` — threads `ingest` through `build_pipeline` / `build_pipeline_task`.
- `api/main.py` — constructs `TurnIngestService` in lifespan, attaches to `app.state.ingest`, calls `ensure_learner_node` + `ensure_scenario_nodes` once at startup.
- `api/routes/session.py` — passes `pool` and `recent_domains` to `build_system_prompt`; calls `ingest.start_session` on connect, `ingest.end_session` on disconnect.
- `api/routes/dev.py` — extended (or sibling `/dev/learner` added) per Open Question #5.

### Confidence

**Level:** Medium

**Rationale:** The relational side (asyncpg + raw SQL + alembic revision) is well-trodden ground in this repo after spec 028 — the pattern is established and the test fixture (`db_pool`) is ready to use. The prompt-builder integration is a one-function rewrite where the `LearnerProfile` Pydantic model already exists and is what the snapshot needs to match. The theme content is a one-time authoring task; quality is the variable, not feasibility.

The two unknowns that bring this to Medium rather than High:

1. **AGE cypher write semantics under asyncpg.** Spec 028 surfaced sharp edges (extended-query protocol, type casts on `drop_graph`, `RESET ALL` wiping `search_path`) that took implementation-time discovery to find. Multi-edge `MERGE` writes through `cypher(...)` may have analogous edges — string parameter passing, transaction interaction with the `init` callback, error reporting on cypher syntax. The AGE smoke test in spec 028 only covered `create_graph` + a trivial cypher round-trip; this spec needs full upsert + edge-merge patterns.
2. **spaCy lemmatization at write time inside the async ingest path.** spaCy is synchronous and the `es_core_news_sm` model is loaded once; per-call lemmatization of a short utterance is fast (~1ms) but happens inside a `conn.transaction()` block. If the spaCy call ever blocks unexpectedly (lazy model loading, GIL contention with another async task), the transaction stays open. Likely fine, worth measuring.

**Validate before proceeding:**

1. **AGE cypher write spike.** Before authoring the full `graph.py` module, spike a single-file proof of concept that does `MERGE (l:Learner {id: 1}) MERGE (v:VocabItem {lemma: 'comer'}) MERGE (l)-[:PRODUCED {at: '...'}]->(v)` through asyncpg, confirms the write commits, and confirms a follow-up `MATCH` returns the expected nodes/edges. ~30 minutes; de-risks the AGE half of the spec.
2. **Confirm the authored theme content is acceptable.** The 50 scenario `domain` identifiers are committed in the spec; the full `prompt` + `target_structures` text is drafted during implementation. A short content review before merge catches scenarios that don't actually elicit the target band (e.g. an A1 scenario that quietly assumes B1 vocabulary).

### Key Decisions

1. **Bundle 029–033 as one spec.** Their data flow is shared end-to-end (`log_turn` → relational + graph → prompt). Splitting produces non-functional intermediates. Pattern: spec 023 bundled four roadmap items for the same reason.
2. **Static band, derived signals.** The band stays config-driven; `L1_reliance`, `speech_fluency`, `error_patterns`, `vocab_strengths` are derived from real history. Auto-promotion (#049) is deferred but the substrate it needs is in place.
3. **Hybrid relational + graph storage.** Relational tables for scalar aggregates (fast `SELECT` on every prompt build); AGE graph for the relationship structure (`PRODUCED`, `MADE_ERROR`, `ENGAGED_WITH`, `RELATES_TO`) that motivated picking AGE in the first place. The prompt does not consume the graph in this slice — that's a future feature. The graph exists so future features have a substrate to build on without a second migration.
4. **Double-write in one transaction.** Aggregate tables and AGE graph both updated inside `conn.transaction()` per turn. Cheap; consistent; avoids materialized-view machinery.
5. **Lemmatize at write time.** spaCy already loaded for `eval/scoring/recast.py`; ~1ms per call; produces a clean primary key on `vocabulary_items.lemma` so reads stay simple.
6. **Graceful degradation on DB failure.** Same posture as spec 023's `missing` counter: log + count + continue. JSONL sink remains the safety net for the observation itself.
7. **`get_session_theme` returns `Theme`, not `str`.** Signature change from the stub. The only consumer needs the full object; returning `str` would force a parallel lookup that defeats the point of a per-band Theme catalog.
8. **Hand-authored theme content, inline.** ~50 scenarios is a one-time authoring task; the spec commits the domain identifiers, implementation fills in the prompt + target_structures. Not worth a separate roadmap item.
9. **Theme rotation is random within band, with cooldown.** No prerequisite logic, no error-pattern-aware recommendation. Those would consume the AGE graph and are out of scope. Cooldown of 3 over a pool of ≥ 8 keeps consecutive sessions varied without forcing an exhaustion-edge case.
10. **One `Learner` node, single profile row.** Single-tenant; primary key 1 with a `CHECK (id = 1)` constraint to enforce it at the schema level. Multi-tenancy would require a schema redesign — explicitly out of scope per project memory.
11. **Sessions table with `theme_domain` column.** Lets `recent_domains` be queried with a simple `SELECT theme_domain FROM sessions ORDER BY started_at DESC LIMIT 3`. Avoids carrying a separate "recent themes" cache in app state.

### Testing Approach

The repo's pytest suite is already wired with the spec-028 `db_pool` and `db_conn` fixtures. New tests use them directly; tests requiring DB skip cleanly when Postgres is unreachable.

**Unit tests (new files):**

- `tests/test_themes.py` (replacing the existing stub):
    - `THEMES_BY_LEVEL` has ≥ 8 entries per band.
    - Every entry is a `Theme` with non-empty `domain` and `prompt`.
    - Domains are unique within a band.
    - `get_session_theme(level="A1", recent_domains=[])` returns a Theme from the A1 pool.
    - `get_session_theme(level="A1", recent_domains=[d1, d2, d3])` never returns one of `{d1, d2, d3}`.
    - With `recent_domains` longer than the pool minus 3, the function still returns *some* theme (cooldown windows to last 3 only).

- `tests/test_learner_profile.py` (new):
    - `LearnerProfileRepo.get` against a freshly migrated DB returns the seeded defaults (band = `A2`, sessions_completed = 0, neutral L1_reliance / fluency, empty error_patterns / vocab_strengths).
    - After inserting 5 fixture turns with known `L1_used` / `fluency_signal` values, `get` returns the expected rolling means.
    - `increment_session_count` advances `sessions_completed` and updates `updated_at`.

- `tests/test_learner_errors.py` (new):
    - `ErrorRepo.record` inserts one row per error into `error_observations` and increments the right `error_counts.category` rows.
    - `ErrorRepo.top_categories(limit=3)` returns the top 3 by count desc, ties broken by `last_seen_at` desc.

- `tests/test_learner_vocabulary.py` (new):
    - `VocabularyRepo.record` lemmatizes a known utterance (`"yo como manzanas todos los días"`) and produces the expected lemmas (`comer`, `manzana`, `día` — content words only).
    - Repeat call increments `production_count` and updates `last_seen_at`; `first_seen_at` is unchanged.
    - `top_recent(limit=5)` returns the most-recently-seen lemmas in order.

- `tests/test_learner_graph.py` (new):
    - After `ensure_learner_node`, a `MATCH (l:Learner) RETURN l` returns one node.
    - After `upsert_vocab(pool, "comer", at)`, the corresponding `(:Learner)-[:PRODUCED]->(:VocabItem {lemma: "comer"})` edge exists.
    - Calling `upsert_vocab` twice for the same lemma produces one node + one edge (`MERGE` semantics).
    - `upsert_error_pattern` analogously for `(:ErrorPattern)`.
    - `link_session_to_scenario` produces one `(:Learner)-[:ENGAGED_WITH]->(:Scenario)` edge per call.

- `tests/test_log_turn_ingestion.py` (new — integration):
    - Builds a `TurnObservation` for a known utterance + errors, calls `TurnIngestService.ingest`, then asserts:
        - One row in `turns`.
        - One row per error in `error_observations`.
        - `error_counts` rows incremented per category.
        - `vocabulary_items` rows upserted per lemma.
        - AGE graph contains the expected `(:Learner)-[:PRODUCED]->(:VocabItem)` and `(:Learner)-[:MADE_ERROR]->(:ErrorPattern)` edges.
    - Repeat call increments counts (idempotency of upserts on a second turn with overlapping lemmas/errors).
    - DB-write failure path: simulate by closing the pool mid-call; assert `ingest_failed` counter on the sink increments and no exception escapes.

**Updated tests:**

- `tests/test_prompts.py` (existing, from spec 023):
    - The byte-identity tests for cold-start (no profile data) must continue to pass — the new `LearnerProfileSnapshot` with neutral defaults must produce the same `LearnerProfile` Pydantic instance as the current `_neutral_profile()` for the cold-start case.
    - Add new tests: with a populated profile (top errors, top vocab), the rendered prompt contains the expected `Known error patterns: …` and `Vocabulary strengths: …` lines.

- `tests/test_tool_handler.py` (existing, from spec 023):
    - Update the handler's constructor calls in tests to thread an `ingest` mock; assert that on the happy path, `ingest.ingest(obs)` is called with the expected `TurnObservation`.
    - On the malformed/missing path, `ingest.ingest` is *not* called (only valid observations are persisted).

- `tests/test_runner.py` (existing): update the constructor signature for `build_pipeline` / `build_pipeline_task` to thread `ingest` through.

**Manual validation (out of pytest):**

- Hold a 5-minute Spanish conversation with the running pipeline. Then:
    - `psql` shows rows in `turns`, `error_observations`, `error_counts`, `vocabulary_items`.
    - `SELECT count FROM error_counts ORDER BY count DESC` matches the project owner's intuition for what errors the learner repeated.
    - `SELECT lemma, production_count FROM vocabulary_items ORDER BY production_count DESC LIMIT 10` shows the most-frequently-used content words.
    - Disconnect, reconnect, start a new session: the system prompt's `## Topic:` is a *different* scenario from the one used in the prior session. The `## Learner` block lists real `Known error patterns` and `Vocabulary strengths` instead of empty lines.
    - `SELECT * FROM ag_catalog.cypher('learner_knowledge', $$ MATCH (l:Learner)-[r]->(n) RETURN type(r), labels(n), count(*) $$) AS (rel agtype, labels agtype, c agtype)` shows the expected counts of `PRODUCED`, `MADE_ERROR`, `ENGAGED_WITH` edges.

- Force a DB outage mid-conversation (`docker compose stop db`). Confirm: the conversation continues, `runtime_turns.jsonl` keeps appending, the `ingest_failed` counter on the sink (visible in `/dev/observations`) increments, no pipeline crash, no session disconnect. Restart Postgres; subsequent turns persist normally.

This is the slice that turns the agent from a band-aware conversational partner into one that remembers. Its job is to make the substrate exist, populate it from the observation stream that's already flowing, and route the result back into the prompt without any new model behavior — the model is the same; what changes is what it knows about the learner.
