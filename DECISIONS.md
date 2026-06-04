# Decisions

## Architectural decisions

### 1. Adapter registry instead of if/elif chains

A dict mapping `ats_source → adapter instance` means the manager never knows which ATS it's talking to. The manager calls `adapter.fetch_applications()` and `adapter.normalize()` — same interface every time. The registry is the only place that knows which ATS exists. Adding a third ATS is one file plus one line in `__init__.py`.

The alternative — branching in the manager — starts reasonable with two sources and becomes unmaintainable at five.

### 2. Normalization raises, validation decides

Each adapter's `normalize()` raises on data it cannot process: `date.fromisoformat("not-a-date")` raises `ValueError`, `STATUS_MAP["PENDING"]` raises `KeyError`. The manager catches both as `normalization_error`.

Structural validation (email present, age ≥ 18) lives in the manager because those rules are internal business rules that apply the same way to every source. Keeping them in the manager means I can change them without touching any adapter.

### 3. Raw sqlite3 in talent_pool instead of an ORM

talent_pool is pure Python, no Django. Adding an ORM (SQLAlchemy, or Django configured for a second service) would be significant overhead for a service that needs one table and three operations. The `repository.py` module is 60 lines and the upsert logic is directly readable.

### 4. First-run window = 7 days (spec default), fixtures dated in the last 24h

The spec default is "7 days ago on first run", and the manager honours it literally: with no stored watermark, `since = now() - 7 days`. For that window to actually pull data, the fixtures have to live inside it, so every Alpha `applied_at` and Beta `submitted_timestamp` is dated within the last day (yesterday → today). The alternative — a far-past sentinel like 2020-01-01 — also pulls everything, but it sidesteps the spec instead of satisfying it. Keeping the real 7-day window means the demo exercises the exact code path a production incremental sync would.

### 5. Fixtures mounted as volumes, not baked into the image

`fake_ats_alpha/fixtures/` and `fake_ats_beta/fixtures/` are mounted into their containers via docker-compose volumes. This means the `candidate.updated` demo scenario (edit a fixture → re-sync → verify changed_fields) can be demonstrated by editing files on the host and restarting the container, without rebuilding the image.

---

## Event bus

Redis pub/sub. The in-process queue was a non-starter: the consumer would run inside talent_pool's process, so there'd be no real boundary to test — and a cross-service decoupling I can't demonstrate isn't worth claiming. A file-based queue works but I'd have to hand-roll polling, file locking, and offset tracking, which is more code and more failure modes than the thing it replaces. Redis pub/sub gives a genuine network boundary between publisher and consumer — the actual topology of production — for one container and near-zero setup. The `EventBus` Protocol keeps the choice cheap: swapping Redis for anything else is a one-file adapter change, not a rewrite.

---

## Things the AI proposed that I rejected

### 1. Abstract base class with `__init_subclass__` registry auto-registration

AI suggested a metaclass approach where each adapter class automatically registers itself on definition. Rejected: it's a clever trick that adds indirection without solving a real problem. At two adapters, a plain dict in `__init__.py` is more readable. If we ever had 20 adapters it might make sense, but that's a hypothetical. The spec explicitly warns against this.

### 2. Celery or async task queue for sync

AI suggested wrapping sync in a Celery task so it runs in the background without blocking the HTTP response. Rejected: the trigger is a curl command and the sync completes in under a second on the local fixture data. Celery adds a worker process, a broker, and task state management for zero benefit in this scope.

### 3. Outbox pattern for event publication

AI proposed writing events to a database table (outbox) before publishing to Redis, to guarantee at-least-once delivery if Redis is down at publish time. Rejected for this scope: the spec explicitly says not to add this unless I think it through and document it. The simple path (publish immediately after upsert) is correct here. A Redis failure during publish loses the event — that's an acceptable trade-off given the demo context.

### 4. Job model as a first-class entity

AI initially modelled Job as a Django model with its own sync flow. Rejected immediately: the spec is explicit that `job_req_id` / `position_code` is just a string that travels with the candidate. There is no Job entity in this assessment.

### 5. Connection pool for gRPC in push_data_manager

AI proposed a module-level channel object to avoid creating a new channel on every sync call. Rejected: the sync is triggered manually with curl, so there are no concurrent calls. A new channel per call is 1–2ms of overhead on a LAN. Connection pooling is the right answer in production under load; it's premature optimisation here.

---

## Trade-offs

### Fixture timestamps coupled to wall-clock

The fixtures carry recent dates (last 24h) so the literal 7-day first-run window pulls them. The cost is a wall-clock coupling: after the first sync the watermark becomes `now()`, so the second-sync no-op only holds if the demo is run *after* the latest fixture timestamp. I keep that latest timestamp early in the day (02:00 UTC) so any normal evaluation time is safely past it. A fully time-independent alternative (a far-past sentinel, or timestamps recomputed at load time) exists; I traded a small, bounded wall-clock assumption for spec-faithful behaviour with deterministic fixed data.

### Django runserver vs gunicorn

Using `python manage.py runserver` avoids adding gunicorn to requirements and keeps the Dockerfile simpler. The trade-off: runserver is single-threaded and not safe for concurrent requests. For a manually-triggered curl demo this is irrelevant. The spec says no production-readiness, so this is the right call.

---

## Things I would do differently

### 1. Run `makemigrations` as part of the Dockerfile

I wrote the initial migration by hand (`0001_initial.py`) rather than generating it with Django. This works but is fragile — if the model changes, someone needs to remember to update the migration manually rather than running `manage.py makemigrations`. I would add a build-time `makemigrations` step in CI (not in the Dockerfile, where the DB path might not match) to catch drift.

### 2. Use `grpc.aio` for an async gRPC server in talent_pool

The current talent_pool uses a `ThreadPoolExecutor` which is fine for low concurrency but holds a thread per in-flight request. For a service that sits under a real sync load, `grpc.aio` + asyncio would be cleaner. I kept the synchronous version because it's simpler to reason about and the spec load doesn't justify it.

---

## Known limitations left as conscious trade-offs

These are real weaknesses I am aware of and chose not to fix, because none is reachable in the scope of this assessment (manual curl trigger, static fixtures, single-threaded sync). I document them here rather than hide them.

### 1. Sync watermark is `datetime.now()`, not `max(applied_at)`

**What happens.** After a sync, `SyncState.last_sync_at` is set to `datetime.now(timezone.utc)`. The next sync pulls records with `applied_at > last_sync_at`. Any candidate created in the source ATS *during* the sync (between the fetch and the `now()` assignment) has an `applied_at` earlier than the recorded watermark, so it is never pulled again. This is the classic high-water-mark gap.

**Why left.** The sync runs manually against static fixtures with no concurrent ingestion, so the gap window is always empty. Tracking the maximum `applied_at` actually seen is also complicated by the two timestamp formats (Alpha ISO 8601 vs Beta unix), which would need parsing back into a common type just to compute the watermark.

**In production.** Page on an immutable, monotonic cursor supplied by the ATS (e.g. an `updated_at` or sequence id) rather than a wall clock. If only a timestamp is available, set the next watermark to the max `applied_at` observed in the batch and re-pull a small overlap window each run — the `(ats_source, external_id)` upsert idempotency absorbs the duplicates, so overlap is safe and a gap is not.

### 2. Alpha `age` is derived from `today()` and can emit a spurious `candidate.updated`

**What happens.** Alpha exposes only `birth_date`, so `AlphaAdapter` computes `age` relative to `date.today()` and that computed value is persisted in talent_pool. When a sync runs after a candidate's birthday, the recomputed age differs from the stored one, so `upsert_candidate` reports `changed_fields=["age"]` and emits a `candidate.updated` even though nothing changed in the source ATS. Beta is immune because its `age` comes straight from the source.

**Why left.** The internal schema and proto model `age` as a persisted `int32` (a fixed decision for this assessment), and Alpha has no age field, so the adapter must derive it. The drift only triggers across a birthday boundary between two separate syncs — it cannot surface in a same-session demo.

**In production.** Persist the stable source value (`birth_date`) and treat `age` as a derived/presentation value computed at read time, so it never participates in change detection. If `age` must stay in the stored schema, exclude derived fields from the `changed_fields` comparison.

### 3. Beta `age=None → 0 → minor_candidate` (missing data classified as minor)

**What happens.** `BetaAdapter.normalize` does `age = raw.get("age") or 0`. A missing or null age collapses to `0`, and the manager's `_validate` then skips the record with reason `minor_candidate`. So a *missing-data* record is mislabeled as an underage candidate. This is inconsistent with Alpha, where a missing/invalid `birth_date` raises and is bucketed as `normalization_error`.

**Why left.** Not reachable with the current Beta fixtures (every record has a valid age). More importantly, `_validate` runs *outside* the `normalize` try/except, so the naive fix (`raw["age"]`) would let `None` reach `None < 18` → `TypeError` → crash the entire sync. The `or 0` fallback is exactly what keeps a missing age from crashing the run; the price is a misleading skip reason for a case that cannot occur with the given data.

**In production.** Validate field presence explicitly and separately from the value check: distinguish "age missing" (a dedicated reason such as `missing_age` / `incomplete_data`) from "age present and < 18" (`minor_candidate`). Either make the adapter raise on missing age — consistent with Alpha, bucketed as `normalization_error` — or add a presence guard in `_validate` before the numeric comparison.
