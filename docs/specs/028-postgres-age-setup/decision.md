# Decision Record: Postgres + Apache AGE Setup

| Field | Value |
|---|---|
| id | 028 |
| status | implemented |
| created | 2026-04-21 |
| spec | [spec.md](./spec.md) |

---

## Context

Spec 023 landed the agent loop end-to-end with `log_turn` observations going to a throwaway `runtime_turns.jsonl` sink. Every planned feature past that point — turn persistence (#026), learner profile (#029), error-pattern tracking (#030), vocabulary tracking (#031), theme selection (#032), and the knowledge-graph learner model (#033) — has been blocked on durable storage. The persistence stack was decided earlier (PostgreSQL + Apache AGE, single-tenant, colocated relational + graph) but no code existed: `hable_ya/db/{connection,hable_ya_db}.py` were empty, `pyproject.toml` still listed the legacy `aiosqlite` dep, and `settings.db_path` still pointed at a SQLite file.

The spec bundled #028 (compose service + driver + pool + migrations + init script) with #041 (init script). The user chose to split DB setup from the learner model so the schema could be co-designed with the consumer specs rather than guessed at in infrastructure; this work is the infrastructure slice.

Implementation surfaced four issues the spec didn't anticipate, all with concrete root causes:

1. **Host port 5432 was held by a system-level Postgres** running on the dev machine, so the compose `db` service couldn't bind.
2. **The `apache/age:release_PG18_1.7.0` image** requires the data volume mounted at `/var/lib/postgresql` (base dir), not the legacy `/var/lib/postgresql/data` — a breaking change introduced by the PG18 image lineage to support `pg_upgrade --link`.
3. **asyncpg runs `RESET ALL` on every pool release**, which wiped the `SET search_path = ag_catalog, ...` set by the pool's `init` callback. After the first release-to-pool, `SHOW search_path` no longer included `ag_catalog`, and every subsequent checkout saw the default path.
4. **pytest-asyncio's default loop scope is function-scoped**, which collides with session-scoped async fixtures — the `db_pool` fixture was being built on one loop and consumed from another, raising "attached to a different loop".

Each of these was resolved inside this slice; none required re-specification.

## Decision

Land the plumbing — nothing more, nothing less. Added:

- A pinned `apache/age:release_PG18_1.7.0` compose service (host port `5433` → container `5432`) with a `pg_isready` healthcheck and a named `pgdata` volume at `/var/lib/postgresql`.
- asyncpg connection pool (`hable_ya/db/connection.py`) with an `init` callback that runs `LOAD 'age'` on every new physical connection.
- Alembic with the async-template `env.py`, target metadata `None`, raw-SQL `op.execute(...)` revisions. DSN is sourced from a new `Settings.async_database_url` property that rewrites `postgresql://` → `postgresql+asyncpg://`.
- First revision `20c019e280a9_enable_extensions` runs `CREATE EXTENSION IF NOT EXISTS age` **and** `ALTER ROLE hable_ya SET search_path = ag_catalog, "$user", public` so the AGE-aware path survives asyncpg's `RESET ALL`.
- Programmatic migration runner `upgrade_to_head()` bridges sync alembic to async callers via `asyncio.to_thread`, wrapped in a 3-attempt tenacity retry for cold-start cases where `pg_isready` is OK but the catalog isn't quite ready.
- `HableYaDB.ping()` near-stub for the health endpoint; `/health` now returns 503 with `status=db_unreachable` when the pool can't round-trip `SELECT 1`.
- `scripts/init_db.py` runs `upgrade_to_head()` and is idempotent.
- Session-scoped `db_pool` fixture in `tests/conftest.py` creates a dedicated `hable_ya_test` DB, runs migrations, yields the pool, drops the DB on teardown. Skips cleanly with a human-readable reason when Postgres is unreachable so the non-DB suite stays green.
- CI gains a `postgres` service container with the same pinned image, a wait-for-postgres step, and `HABLE_YA_DATABASE_URL` exported into the pytest step.

**No learner schema, no AGE graph schema, no JSONL-sink replacement.** Those belong to the consumer specs.

---

## Alternatives Considered

### Migration tool

**Option A — Home-grown runner.** ~50 lines: read `migrations/*.sql` in lexical order, track applied files in a `_schema_migrations` table, apply in a transaction.
- Pros: zero deps beyond asyncpg; no framework surface to learn.
- Cons: reinvents a solved problem; every consumer spec adds to the runner's scope.

**Option B — Alembic (async template).**
- Pros: standard CLI, conventional layout, programmatic `alembic.command.upgrade(...)` that embeds cleanly in startup, collision-safe revision IDs, autogeneration available if we ever adopt SQLAlchemy models.
- Cons: drags in `alembic` + `sqlalchemy[asyncio]`; async env.py needs the `asyncio.to_thread` bridge (see "Alembic-in-async" below).

**Chosen:** B. The user explicitly selected alembic in spec resolution. Confirmed during impl: the scaffolding is small (~80 lines total across `alembic.ini`, `env.py`, `migrations.py`), the worker-thread bridge is a well-known pattern, and consumer specs inherit a CLI they can use directly (`uv run alembic revision -m "..."`) instead of a bespoke runner they'd have to understand.

### Alembic-in-async bridge

**Option A — Run `alembic.command.upgrade` directly in an event loop.**
- Cons: raises `RuntimeError: asyncio.run cannot be called from a running event loop` because the async `env.py` template does `asyncio.run(run_async_migrations())` internally.

**Option B — `await asyncio.to_thread(alembic.command.upgrade, cfg, "head")`.**
- Pros: worker thread has no running loop, so `env.py`'s `asyncio.run(...)` works. Caller gets a normal `await`.
- Cons: one extra abstraction line in `migrations.py`.

**Chosen:** B. Standard pattern; called out in alembic's async template docs.

### AGE search_path persistence

**Option A — Rely on asyncpg `init` callback only** (`SET search_path = ag_catalog, "$user", public;` on every new physical connection).
- Problem: asyncpg runs `RESET ALL` when a connection is released to the pool. This wipes non-default session GUCs including `search_path`. On the next checkout, the default path (just `"$user", public`) is in effect, and unqualified AGE calls fail.

**Option B — `ALTER ROLE hable_ya SET search_path = ...` in the migration.**
- Pros: persists at the role level, survives `RESET ALL` (RESET restores role defaults, not compile-time defaults).
- Cons: cluster-wide effect for that role. Acceptable in single-tenant on-device deployment.

**Option C — `SET LOCAL` in an outer transaction.** Not viable — not every caller uses transactions.

**Chosen:** B, with A kept as belt-and-suspenders in the init callback. The callback sets the path at connection creation; the role default keeps it correct after every release-to-pool.

### Host port mapping

**Option A — `5432:5432` as the spec suggested.** Fails on this dev machine (and anywhere a system Postgres is running).

**Option B — `5433:5432`.** `.env.example` and `config.py` default to `localhost:5433`. In-compose `app` service overrides `HABLE_YA_DATABASE_URL` to `db:5432`, so container→container traffic doesn't care about the host mapping.

**Chosen:** B. Durable, avoids the coordination cost of asking users to stop existing Postgres instances. CI is unaffected (no collision on GitHub runners).

### Test DB strategy

**Option A — Spin up per-session container via `testcontainers-python`.**
- Pros: fully isolated; no dependency on an externally-running Postgres.
- Cons: heavy dev dep; slow on CI without Docker-in-Docker; per-session startup cost.

**Option B — `hable_ya_test` database on the same compose Postgres.**
- Pros: no extra deps; fast fixture setup; CI uses the service container that's already present.
- Cons: requires a running Postgres; drops+recreates the test DB at session start.

**Chosen:** B (from spec resolution). The fixture cleanly skips with a reachability reason when Postgres is down, so non-DB tests stay green on any machine.

### Retry strategy for `upgrade_to_head`

**Option A — No retry.** Fail fast on DB unreachability; compose `depends_on: condition: service_healthy` is the gate.
- Cons: on cold `docker compose up`, there's a narrow window where `pg_isready` returns OK but the catalog is still initializing. `CREATE EXTENSION` occasionally races with the extension catalog.

**Option B — Tenacity 3 × 2s on `(CannotConnectNowError, ConnectionError, OSError)`.**
- Pros: absorbs cold-start noise; worst-case 6s added to startup; fails fast if Postgres is truly down.
- Cons: one more retry loop in the codebase.

**Chosen:** B (from plan resolution). Considered extracting a shared retry helper with `warmup_llm` during simplify review — declined: the two retry configs differ in attempts, delay, exception types, and logging. Shared helper would be a false abstraction.

---

## Tradeoffs

- **Infrastructure only.** This slice buys a queryable, migrated, health-checked DB with AGE loaded — and nothing else the learner cares about. The `runtime_turns.jsonl` sink still catches observations; the learner profile still reads defaults; `HABLE_YA_TOOLS` is unchanged. Consumer specs do the work the user will actually feel.
- **Alembic + SQLAlchemy as transitive deps.** Pay the cost of two more packages in the lockfile and a small amount of scaffolding (~80 lines) in exchange for a well-known migration CLI and a conventional layout. SQLAlchemy is imported only in `env.py` — the runtime uses raw asyncpg.
- **Cluster-wide `ALTER ROLE` search_path.** Cleaner than a per-DB setting but does touch the role's default everywhere. Acceptable under the single-tenant constraint; would need rethinking under multi-tenant (which is explicitly out of scope — per project memory).
- **Session-scoped pytest event loop.** Setting `asyncio_default_fixture_loop_scope = "session"` and `asyncio_default_test_loop_scope = "session"` globally means all async tests now share one loop. Trade-off: a rogue test that hangs the loop could bleed into other tests; gain: session-scoped `db_pool` works without bespoke per-fixture `loop_scope` annotations. No existing tests were affected.
- **Host port 5433 default.** Cleaner than asking users to stop system Postgres, but now the DSN in `.env.example` differs from the conventional `5432`. Compose's `environment:` override handles in-compose runs transparently; documented inline.

---

### Spec Divergence

| Spec Said | What Was Built | Reason |
|---|---|---|
| `ports: "5432:5432"` on the compose `db` service | `ports: "5433:5432"` | Host port 5432 was held by a system-level Postgres on the dev machine; remapping avoids a durable coordination cost. In-compose traffic unaffected (uses `db:5432`). |
| Default DSN `postgresql://...@localhost:5432/hable_ya` | `postgresql://...@localhost:5433/hable_ya` | Follows from the port remap. |
| Volume mount `pgdata:/var/lib/postgresql/data` | `pgdata:/var/lib/postgresql` | PG18 image changed the convention (data now lives under `<base>/<major>/docker/`); mounting at the legacy path makes the image refuse to start. |
| AGE search_path set via asyncpg `init` callback only | Also `ALTER ROLE hable_ya SET search_path = ...` in the first migration | asyncpg's `RESET ALL` on pool release wiped the init-callback SET. Role-level setting survives RESET. |
| Not specified | `asyncio_default_fixture_loop_scope = "session"` and `asyncio_default_test_loop_scope = "session"` in `pyproject.toml` | pytest-asyncio 1.x defaults to function-scoped loops, which collide with session-scoped async fixtures. Required for `db_pool` to work. |
| Spec test ran `SELECT drop_graph('smoke', true)` directly | Uses explicit cast: `SELECT drop_graph('smoke'::name, true)` | asyncpg sends string literals as `unknown` via extended-query protocol; AGE's `drop_graph(name, boolean)` overload resolution fails on `(unknown, boolean)`. Explicit `::name` cast resolves it. |
| DSN async-form rewrite inline in `env.py` | `Settings.async_database_url` property | Surfaced in simplify review — DSN manipulation belongs with the config, not scattered across modules. Single-line change; no spec impact. |

---

## Spec Gaps Exposed

These aren't errors in the spec — they're details the spec didn't know to flag, surfaced only by running the build:

1. **PG18 image volume-path convention.** The spec called for `pgdata:/var/lib/postgresql/data`, matching older Postgres Docker image versions. Worth noting in `ARCHITECTURE.md` for future image pins.
2. **asyncpg pool-release `RESET ALL`.** The spec assumed the `init` callback's `SET` would persist; it doesn't, and the workaround (ALTER ROLE) is non-obvious. Future specs that touch session-scoped GUCs should use role defaults or re-set on every checkout.
3. **pytest-asyncio loop scope.** Not addressed by spec or by existing `conftest.py` (which was empty). Every future async fixture using session scope inherits the setting; single knob, works cluster-wide for the suite.
4. **asyncpg extended-query + AGE overload resolution.** Future AGE usage with multi-arg functions (`drop_graph`, anything taking `name` or other non-`text` types) will need explicit casts. Worth a short note in a future spec that builds the AGE graph schema (#033).

None of these warrant a spec revision now — all four are implementation notes that belong with the learner-model spec when it designs its schema.

---

## Test Evidence

Full suite with Postgres available (from compose `db` service):

```
$ uv run pytest tests/ -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
configfile: pyproject.toml
plugins: anyio-4.13.0, asyncio-1.3.0, typeguard-4.5.1, cov-7.1.0
asyncio: mode=Mode.AUTO, debug=False,
  asyncio_default_fixture_loop_scope=session,
  asyncio_default_test_loop_scope=session
collected 116 items

... [110 pre-existing tests passing] ...
tests/test_db.py::test_pool_lifecycle PASSED                             [ 95%]
tests/test_db.py::test_age_loaded_on_acquire PASSED                      [ 96%]
tests/test_db.py::test_age_extension_present PASSED                      [ 97%]
tests/test_db.py::test_upgrade_to_head_idempotent PASSED                 [ 97%]
tests/test_db.py::test_age_smoke_create_graph_and_cypher PASSED          [ 98%]
tests/test_init_db.py::test_upgrade_to_head_twice_is_noop PASSED         [ 99%]
tests/test_health.py::test_health_returns_200_after_warmup PASSED        [ 99%]
tests/test_health.py::test_health_returns_503_before_warmup_completes    PASSED
tests/test_health.py::test_health_returns_503_when_db_unreachable        PASSED
tests/test_health.py::test_ws_session_refused_while_warming_up           PASSED

======================= 116 passed, 9 warnings in 4.39s ========================
```

Skip behavior with Postgres down (demonstrates non-DB suite stays green on machines without Docker):

```
$ docker compose stop db && uv run pytest tests/
================== 110 passed, 6 skipped, 9 warnings in 4.10s ==================
```

Init-script idempotency:

```
$ uv run python scripts/init_db.py
INFO hable_ya.db.migrations Running alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 20c019e280a9, enable_extensions

$ uv run python scripts/init_db.py
INFO hable_ya.db.migrations Running alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
# (no "Running upgrade ..." line — already at head)
```

psql verification:

```
$ docker exec hable-ya-db-1 psql -U hable_ya -d hable_ya -c '\dx'
                          List of installed extensions
  Name   | Version | Default version |   Schema   |         Description
---------+---------+-----------------+------------+------------------------------
 age     | 1.7.0   | 1.7.0           | ag_catalog | AGE database extension
 plpgsql | 1.0     | 1.0             | pg_catalog | PL/pgSQL procedural language

$ docker exec hable-ya-db-1 psql -U hable_ya -d hable_ya -c \
  'SELECT version_num FROM alembic_version;'
 version_num
--------------
 20c019e280a9
```

AGE cypher smoke (manual, demonstrating end-to-end extension + search_path):

```
$ docker exec hable-ya-db-1 psql -U hable_ya -d hable_ya \
    -c "LOAD 'age'; SET search_path = ag_catalog, public; \
        SELECT create_graph('smoke_manual'); \
        SELECT drop_graph('smoke_manual', true);"
LOAD
NOTICE:  graph "smoke_manual" has been created
NOTICE:  drop cascades to 2 other objects
NOTICE:  graph "smoke_manual" has been dropped
```

Lint + type + hygiene:

```
$ uv run ruff check ...              # All checks passed!
$ uv run mypy ...                    # Success: no issues found in 42 source files
$ grep -rn 'aiosqlite\|db_path' hable_ya/ api/ scripts/ tests/ || echo clean
clean
```
