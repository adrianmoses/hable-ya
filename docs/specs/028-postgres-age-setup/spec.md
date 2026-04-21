# Spec: Postgres + Apache AGE Setup — Service, Async Driver, Migration Runner, Init Script

| Field | Value |
|---|---|
| id | 028 |
| status | implemented |
| created | 2026-04-21 |
| covers roadmap | #028, #041 |
| decision | [decision.md](./decision.md) |

---

## Why

The runtime has been producing `log_turn` observations since spec 023, but they are written to a throwaway JSONL sink (`runtime_turns.jsonl`). Every downstream runtime feature the roadmap calls out — turn persistence (#026), learner profile (#029), error-pattern tracking (#030), vocabulary tracking (#031), theme selection (#032), and especially the knowledge-graph learner model (#033) — requires durable, queryable storage with graph semantics. Architecture and product decisions (recorded in ARCHITECTURE.md and project memory) set that stack as **PostgreSQL + Apache AGE**: relational tables for sessions/turns/vocab/errors and an AGE graph for the knowledge-graph model, colocated in the same Postgres instance to keep single-tenant deployment simple.

Today `hable_ya/db/{connection,hable_ya_db}.py` are empty, `scripts/init_db.py` is a one-line stub, `pyproject.toml` still lists the legacy `aiosqlite` dependency, and `settings.db_path` still points at a SQLite file. This spec lands the *plumbing only*: a Postgres+AGE service in docker-compose, asyncpg driver, connection pool with AGE bootstrapping, a migration runner, an idempotent init script, and a health-check wiring. **No learner schema in this spec** — tables and AGE graph labels belong to the learner-model spec that consumes them.

### Consumer Impact

- **End user (learner):** No visible change. The agent still responds, the orb still animates, the JSONL sink still collects observations. This is pure infrastructure.
- **Project owner (researcher/developer):** On a fresh checkout, `docker compose up db` + `uv run python scripts/init_db.py` yields a Postgres instance with the AGE extension loaded, a tracked migration history, and a healthy connection pool when the app starts. `psql` access is available for inspection. Health endpoint now reports DB readiness as well as llama.cpp readiness. This is the moment the SQLite-era inconsistencies (`aiosqlite` dep, `db_path` config) are cleaned up.
- **Downstream features:** #026 (turn persistence) becomes a drop-in replacement of the JSONL sink. #029–#033 (learner model) has a stable target to schema against. #033 (AGE graph) has extensions already loaded. #042 (artifact registry) has a place to live.

### Roadmap Fit

Bundles two planned items:

- **#028** Postgres + Apache AGE: docker-compose service, async driver (asyncpg), schema, migrations, init script.
- **#041** Database init script — trivially a sub-task of #028; not worth a separate spec.

Explicitly *not* bundled: the learner schema itself (#026, #029–#033). Those are the consumers; splitting them from infrastructure lets the schema decisions be made with the learner-model shape in view, and lets this spec land a reviewable, testable slice without committing prematurely to table shapes.

Dependencies:

- Upstream: none strictly required. The voice pipeline (#021) and agent loop (#023) are independent of this — the JSONL sink keeps working until #026 replaces it.
- Downstream (unblocked by this): #026, #029, #030, #031, #032, #033, #042.

---

## What

### Acceptance Criteria

On a fresh checkout with Docker installed:

- [ ] `docker compose up db` starts a Postgres service with Apache AGE available as an installable extension. The container survives a restart without data loss (named volume).
- [ ] `uv sync` installs `asyncpg` and does **not** install `aiosqlite` (removed from `pyproject.toml`).
- [ ] `uv run python scripts/init_db.py` is idempotent: first run creates the database (if needed), runs all migrations, reports what it applied; second run reports "no pending migrations" and exits 0 without re-applying anything.
- [ ] The first alembic revision (`enable_extensions`, at `hable_ya/db/alembic/versions/<rev>_enable_extensions.py`) runs `CREATE EXTENSION IF NOT EXISTS age;` in `upgrade()`. After it has run, `SELECT extname FROM pg_extension WHERE extname = 'age'` returns one row, and the alembic `alembic_version` table records its revision id.
- [ ] `hable_ya.config.settings.database_url` replaces `db_path`. `.env.example` is updated. `HABLE_YA_DB_PATH` is removed from configuration; `HABLE_YA_DATABASE_URL` is documented with a working default.
- [ ] `hable_ya.db.connection` exposes:
    - `async def open_pool() -> asyncpg.Pool` — opens the pool, returns it.
    - `async def close_pool(pool: asyncpg.Pool) -> None` — closes cleanly.
    - Every acquired connection has AGE loaded and `search_path` set so that `ag_catalog` functions (`create_graph`, `cypher`) are callable without qualification.
- [ ] `hable_ya.db.migrations.upgrade_to_head()` is an async wrapper over `alembic.command.upgrade(cfg, "head")` that both `scripts/init_db.py` and the test fixture call — so migrations apply identically in dev, CI, and tests.
- [ ] The FastAPI lifespan in `api/main.py` opens the pool at startup, attaches it to `app.state.db_pool`, and closes it at shutdown. A failed pool open surfaces as a clear startup error, not a silent degradation.
- [ ] `GET /health` returns **503** while the DB pool is not yet open (or `SELECT 1` fails), and **200** once both the llama.cpp warmup and a DB round-trip have succeeded. Existing llama.cpp warmup behavior is preserved.
- [ ] `tests/test_db.py` covers: pool open/close, migration runner applies migrations once (idempotency), AGE extension is loaded on connections acquired from the pool, and a round-trip `create_graph` → simple cypher insert → cypher select works. Tests skip cleanly with a clear message when no DB is reachable (env gate), so the rest of the suite stays green on machines without Docker running.
- [ ] CI (`.github/workflows/ci.yml`) adds a Postgres+AGE service container so `test_db.py` runs on PRs.
- [ ] `pytest` passes locally (with Docker up) and in CI.

### Non-Goals

- **No learner schema.** No `sessions`, `turns`, `errors`, `vocabulary`, or `learner_profile` tables. Those belong to #026 and #029–#031.
- **No AGE graph schema.** No `create_graph('learner_model')` outside the smoke test, no node labels, no edge labels, no cypher DSL helpers. That is #033.
- **No JSONL sink replacement.** `runtime_turns.jsonl` keeps working. #026 owns the swap.
- **No production hardening.** No TLS/SSL, no superuser separation, no role-based access, no backup/restore, no connection limits beyond sensible pool defaults. Single-tenant, local dev for now.
- **No multi-tenancy, RLS, or authorization.** Still one learner per deployment.
- **No migration downgrade / rollback path.** Forward-only, single-tenant — if a migration needs to be reverted we edit + reapply against a fresh DB.
- **No ORM.** Raw asyncpg + SQL strings + migration files. Adopting SQLAlchemy/SQLModel is out of scope and can be revisited once the schema is real.
- **No cypher helper abstraction.** The AGE smoke test calls `ag_catalog.cypher(...)` directly so we know the extension works; any `graph.add_node(...)` style API is #033's job.

### Open Questions

1. ~~**Docker image — `apache/age:latest` or a pinned tag?**~~ **Resolved: `apache/age:release_PG18_1.7.0`.** Pinned, PG18, current AGE release.

2. ~~**Migration runner — home-grown or a library (alembic / yoyo-migrations)?**~~ **Resolved: Alembic.** Standard async-template setup using SQLAlchemy Core (no ORM models) with `op.execute(...)` for raw SQL. Tradeoff accepted: adds `alembic` + `sqlalchemy[asyncio]` deps (and a small amount of alembic scaffolding — `alembic.ini`, `env.py`) in exchange for a well-known migration CLI, autogeneration of revision IDs, and a conventional layout the learner-model spec can extend without reinventing.

3. ~~**Test strategy — docker-compose DB or testcontainers?**~~ **Resolved: docker-compose DB.** A separate `hable_ya_test` database on the same compose Postgres instance, created and dropped by the test session fixture. Per project memory, DB tests must hit real Postgres; this satisfies that without pulling in testcontainers.

4. ~~**Database bootstrap ownership — init_db or compose?**~~ **Resolved: compose creates the DB and role** via `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`. `scripts/init_db.py` only runs `alembic upgrade head`. Keeps the script role-independent and avoids needing superuser creds in app config.

---

## How

### Approach

**Docker compose service.**

Add a `db` service to `docker-compose.yml`:

```yaml
db:
  image: apache/age:release_PG18_1.7.0
  ports:
    - "5432:5432"
  environment:
    POSTGRES_USER: hable_ya
    POSTGRES_PASSWORD: hable_ya
    POSTGRES_DB: hable_ya
  volumes:
    - pgdata:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U hable_ya -d hable_ya"]
    interval: 5s
    timeout: 3s
    retries: 10

volumes:
  pgdata:
```

The `app` service gets `depends_on: [llama, db]` with `condition: service_healthy` on `db`.

**Dependency swap.**

- `pyproject.toml`: remove `aiosqlite>=0.20.0`. Add:
    - `asyncpg>=0.30.0` — runtime driver for the app's connection pool.
    - `alembic>=1.13.0` — migration tool.
    - `sqlalchemy[asyncio]>=2.0.0` — required by alembic; used **only** to provide the async engine the alembic `env.py` binds to. No ORM models, no Core DSL at runtime — the app continues to use raw asyncpg + SQL strings.
- Update the uv lockfile.

**Config.**

- `hable_ya/config.py`:
    - Remove `db_path: str = "./hable_ya.db"`.
    - Add `database_url: str = "postgresql://hable_ya:hable_ya@localhost:5432/hable_ya"` (env: `HABLE_YA_DATABASE_URL`).
    - Add `db_pool_min_size: int = 1` and `db_pool_max_size: int = 4` (env: `HABLE_YA_DB_POOL_MIN_SIZE` / `_MAX_SIZE`). Defaults match single-tenant + `--parallel 4` on llama.cpp.
    - Add `db_pool_timeout_seconds: float = 5.0`.
- `.env.example`: replace `HABLE_YA_DB_PATH` with `HABLE_YA_DATABASE_URL` (with a comment that the compose default works out of the box). Document the pool knobs.

**Connection pool + AGE bootstrap.**

- New: `hable_ya/db/connection.py`:
    - `async def open_pool() -> asyncpg.Pool` — builds the pool with `asyncpg.create_pool(dsn=settings.database_url, min_size=..., max_size=..., command_timeout=..., init=_init_connection)`.
    - `_init_connection(conn)` is an `init=` callback that runs on every new connection:
        ```
        LOAD 'age';
        SET search_path = ag_catalog, "$user", public;
        ```
      This is required — AGE is a dynamic extension and functions like `cypher(...)` and `create_graph(...)` are only resolvable after `LOAD 'age'` on that specific session.
    - `async def close_pool(pool)` — `await pool.close()`.
- **Alembic scaffolding** (new):
    - `alembic.ini` at repo root. `script_location = hable_ya/db/alembic`. `sqlalchemy.url` is not hard-coded there — it's set programmatically in `env.py` from `settings.database_url`, rewritten to the SQLAlchemy async driver form (`postgresql+asyncpg://...`).
    - `hable_ya/db/alembic/env.py` — async-template `env.py`. Imports `settings`, builds an `AsyncEngine` with `postgresql+asyncpg://` DSN, runs migrations via the documented SQLAlchemy async pattern (`async with engine.connect() as conn: await conn.run_sync(do_run_migrations)`). Target metadata is `None` (we have no ORM models); autogeneration is therefore unused — revisions are authored by hand.
    - `hable_ya/db/alembic/script.py.mako` — default template from `alembic init --template async`, left untouched.
    - `hable_ya/db/alembic/versions/<rev>_enable_extensions.py` — first revision:
        ```python
        from alembic import op

        revision = "<autogenerated>"
        down_revision = None

        def upgrade() -> None:
            op.execute("CREATE EXTENSION IF NOT EXISTS age;")

        def downgrade() -> None:
            op.execute("DROP EXTENSION IF EXISTS age;")
        ```
      (Additional extensions or tables the learner-model spec decides to add will ship as later revisions owned by that spec.)
    - The legacy `hable_ya/db/migrations/001_initial.sql` stub is removed.
- **Programmatic migration runner.** `hable_ya/db/migrations.py` exposes `async def upgrade_to_head() -> None`. It invokes `alembic.command.upgrade(config, "head")` — alembic's CLI is a thin wrapper over this, and calling it programmatically is the documented approach for embedding it in app startup / init scripts. `scripts/init_db.py` and the test `db_pool` fixture both call this function (rather than shelling out to the `alembic` CLI) so that behavior stays identical across dev, tests, and CI.
- `hable_ya/db/hable_ya_db.py` stays a near-stub for this slice — just enough to define the class and a health-check method (`ping() -> bool` that does `SELECT 1`). The real query layer lives in the consumer specs.
- `hable_ya/db/__init__.py` exports `open_pool`, `close_pool`, `upgrade_to_head`, `HableYaDB`.

**App lifespan + health.**

- `api/main.py`: extend the existing lifespan (the one that warms llama.cpp today) to also call `upgrade_to_head()` and then open the pool at startup, store `app.state.db_pool`, and close at shutdown. A DB failure on startup is fatal — we log loudly and raise rather than start in a half-working state, since every runtime feature past spec 023 will depend on the pool.
- `api/routes/health.py`: the 503 → 200 gate now requires both (a) llama.cpp warmup complete (existing) and (b) `SELECT 1` succeeds against the pool. Fast (< 50ms) — the route is already polled by compose healthcheck.

**Init script.**

- `scripts/init_db.py` (replaces current one-liner stub):
    - CLI entry point (no args in v1).
    - Calls `upgrade_to_head()` (which wraps `alembic.command.upgrade(cfg, "head")`), prints the alembic output verbatim so the user sees which revisions ran (or that the DB was already up to date).
    - Exits non-zero on any error. Idempotent (alembic tracks applied revisions in its `alembic_version` table). Safe to run on every dev start-up (`docker compose up` + this script + `uvicorn`).
    - Assumes the DB and role already exist (compose creates them). Does not connect as superuser.

**Testing wiring (detailed in Testing Approach).**

- `tests/conftest.py`: a new session-scoped `db_pool` fixture that connects to `hable_ya_test` (a separate database on the same compose Postgres), creates the DB if it doesn't exist (connects to `postgres` admin DB to issue `CREATE DATABASE`), runs `upgrade_to_head()` against it with the test DSN, yields the pool, drops the DB at teardown. Skips the whole suite of DB tests with a clear message if `HABLE_YA_DATABASE_URL` is unset or unreachable.
- `.github/workflows/ci.yml`: add a `postgres` service using the same pinned `apache/age:release_PG18_1.7.0` image, env to match the test config, and a `pg_isready` wait step before `pytest`.

**Graceful operation guarantees.**

- Startup: DB down → clear fatal log, no half-open app.
- Runtime: pool connection loss → asyncpg's automatic reconnect handles it transparently for most queries; for the specific `SELECT 1` health check, a timeout falls through to 503.
- Shutdown: pool close is awaited in lifespan teardown; no zombie connections.

### Confidence

**Level:** High

**Rationale:** asyncpg + Postgres is a mature, well-documented combination. Apache AGE is the one unusual element, but its integration model is narrow (`LOAD 'age'` per connection + `search_path`), well-documented, and already used in production by small projects. Alembic's async template is a known pattern and its programmatic API (`alembic.command.upgrade`) is a well-worn path for embedding migrations in app startup and test fixtures. This spec lands plumbing with strict scope boundaries (no schema, no consumer logic), which keeps the blast radius small. All four open questions are resolved.

Minor unknowns, none blocking:

- Whether AGE's `LOAD 'age'` on every connection acquire has meaningful overhead at our scale — almost certainly not (single-tenant, pool size 4), but the metric is observable on pool init if it ever matters.
- Whether asyncpg's `init=` callback runs on both freshly-created and pool-recycled connections — yes, per docs; validated in the AGE-load test.
- First-run pairing of alembic's async `env.py` against the pinned PG18 + AGE image — minor risk of boilerplate friction; mitigated by the published alembic async template plus a smoke test that runs `upgrade_to_head` end-to-end against a real DB.

### Key Decisions

1. **Plumbing only; no schema.** The value of this slice is a migrated, pooled, health-checked DB with AGE enabled. Schema decisions belong to the consumers. This keeps the spec small, reviewable, and lets consumer specs co-design table and graph shapes against real learner-model requirements.
2. **Alembic with no ORM.** Alembic is used purely as a migration driver — revisions are hand-authored Python files containing raw `op.execute("...")` SQL. SQLAlchemy is pulled in as an alembic dependency and its async engine is used only inside `env.py`. No ORM models, no metadata autogeneration. The tradeoff: two extra deps and a small amount of scaffolding (`alembic.ini`, `env.py`, `script.py.mako`) in exchange for a standard CLI, conventional layout the learner-model spec can extend, and programmatic `alembic.command.upgrade` that embeds cleanly in startup and test fixtures.
3. **AGE `LOAD` + `search_path` in the pool's `init` callback.** The natural place — every acquired connection is ready to call `create_graph`, `cypher`, etc. without the caller knowing AGE is special.
4. **asyncpg, not psycopg3.** asyncpg is faster, more idiomatic for pure-async workloads, and the project has no prepared-statement / psycopg-specific needs that would argue the other way.
5. **Pool defaults 1/4.** Matches single-tenant + `--parallel 4` on llama.cpp. Tunable via env.
6. **Fatal startup on DB failure.** No half-working app; every runtime feature past spec 023 will be a DB consumer, so a silent fallback would just mask problems.
7. **Compose creates the DB and role; init_db only runs migrations.** Keeps `init_db.py` role-agnostic and avoids baking superuser creds into app config.
8. **Tests against a separate `hable_ya_test` database on the same compose Postgres.** Per project memory, DB tests must hit a real Postgres; a separate DB on the same instance avoids testcontainer complexity while keeping prod and test data disjoint.

### Testing Approach

The repo's pytest suite already exists (`OVERVIEW.md §Testing Suite`). `tests/test_db.py` is currently a docstring-only stub — this spec fills it.

**New / updated tests:**

- `tests/conftest.py` (new fixtures):
    - `db_pool` (session-scoped, async): ensures `hable_ya_test` DB exists (creating it from the `postgres` admin DB if not), opens a pool against it, runs migrations, yields the pool, closes + drops the DB at teardown. Skips with a clear message if DB is unreachable.
    - `db_conn` (function-scoped): acquires a connection from the session pool for a single test, in a transaction that rolls back on teardown so tests are isolated without needing DB recreation per-test.

- `tests/test_db.py`:
    - **Pool lifecycle**: `open_pool()` returns a live pool; `close_pool()` closes without error.
    - **AGE loaded on acquire**: a connection from the pool can call `SELECT * FROM ag_catalog.ag_graph` without `relation does not exist` errors. Schema-search-path sanity check (`SHOW search_path` contains `ag_catalog`).
    - **Migration idempotency**: against a freshly-created test DB, `upgrade_to_head()` applies the initial revision and records it in `alembic_version`; a second call is a no-op and doesn't error. `SELECT version_num FROM alembic_version` returns the revision id both times.
    - **Extension presence**: after migrations, `SELECT extname FROM pg_extension WHERE extname = 'age'` returns one row.
    - **AGE smoke test** (the real proof AGE works end-to-end): `SELECT ag_catalog.create_graph('smoke_test_graph')`, run a trivial cypher insert + select via `ag_catalog.cypher(...)`, clean up with `drop_graph`. This is the one test that'd catch an incompatible AGE image or a broken `LOAD 'age'` callback.

- `tests/test_health.py` (new or extended):
    - With the pool open, `GET /health` returns 200.
    - With the pool closed (simulate by closing `app.state.db_pool` mid-test), `GET /health` returns 503.

- `tests/test_init_db.py` (new):
    - Running `init_db.main()` twice in one test doesn't raise and the second call reports no pending migrations (captures stdout or inspects the tracking table).

**CI:**

- Add a `postgres` service container to `.github/workflows/ci.yml` using the same pinned `apache/age` image, `POSTGRES_*` env vars, and a `pg_isready` wait step. Export `HABLE_YA_DATABASE_URL` in the test step so the `db_pool` fixture has a target.

**Manual validation (out of pytest, human-run):**

- `docker compose up db` → `uv run python scripts/init_db.py` → `psql postgresql://hable_ya:hable_ya@localhost:5432/hable_ya -c "\dx"` shows `age`, and `SELECT version_num FROM alembic_version` shows the initial revision id.
- `uvicorn api.main:app` starts; `curl localhost:8000/health` returns 200; stop the db (`docker compose stop db`) and re-hit `/health` — expect 503.
- `uv run python scripts/init_db.py` a second time — output should read "no pending migrations" and exit 0.

This is the first spec in the runtime workstream that commits to durable state. Its job is to get the substrate boringly correct so the learner-model spec that follows only has to think about its own tables and graph.
