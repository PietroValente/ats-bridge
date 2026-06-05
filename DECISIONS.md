# Decisions

## Architectural decisions

### 1. Adapter registry instead of if/elif chains

A dict mapping `ats_source → adapter instance` means the manager never knows which ATS it's talking to. The manager calls `adapter.fetch_applications()` and `adapter.normalize()` — same interface every time. The registry is the only place that knows which ATS exists. Adding a third ATS is one file plus one line in `__init__.py`.

The alternative — branching in the manager — starts reasonable with two sources and becomes unmaintainable at five.

A factory function is overkill here: it adds a construction layer without removing the branching — you still need an `if/elif` inside the factory to decide which adapter to build. The registry skips that entirely by holding instances directly.

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

### 1. Job model as a first-class entity

AI initially modelled Job as a Django model with its own sync flow. Rejected immediately: the spec is explicit that `job_req_id` / `position_code` is just a string that travels with the candidate. There is no Job entity in this assessment.

### 2. Far-past sentinel instead of 7-day window

The initial implementation used `datetime(2020, 1, 1)` as the first-run sentinel — the reasoning was that it guaranteed all fixtures would always be pulled regardless of their timestamps, making tests more stable. Rejected: the spec is explicit that the default window is 7 days. Hiding behind a far-past date satisfies the behaviour without satisfying the requirement. I kept the literal `timedelta(days=7)` and re-dated the fixtures to stay inside the window, so the code exercises the exact path a production incremental sync would.

### 3. Autoincrement integer as candidate primary key

AI generated `candidate_pk` as a SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`. Rejected: the pk leaves the service — it travels in every `candidate.created` / `candidate.updated` event as the candidate's identity. An autoincrement counter resets to 1 whenever the database is wiped and rebuilt (which is exactly how the demo is reset, `down -v`), so after a reset `pk=1` points at a different candidate than before. Any downstream consumer that stored the pk as a reference would silently corrupt. I switched to a UUID generated at insert time: globally unique, reset-safe, no central counter to coordinate. The cost is a wider key (36-char string vs int); irrelevant at this scale.

---

## Trade-offs

### Raw SQL in talent_pool vs ORM

`talent_pool` uses raw sqlite3 with no ORM. The trade-off: schema changes require manual SQL updates with no tooling to catch drift, and the upsert logic — SELECT, compare fields, conditional UPDATE — is more verbose than an ORM equivalent would be. The other side: `talent_pool` is pure Python with one table and three operations. An ORM (SQLAlchemy) would add a significant dependency and a layer of indirection for no reduction in actual complexity — the change-detection logic still has to be written by hand regardless, because no ORM computes `changed_fields` for you. The verbosity is contained in one 100-line file; the trade-off is worth it.

### Redis pub/sub vs Kafka

Redis pub/sub is the right call for this scope: one container, zero configuration, and it gives a real network boundary between publisher and consumer. The trade-off is durability — pub/sub has no persistence, so any event published while `event_logger` is down is lost permanently. Kafka solves this with a persistent, replayable log, but it requires a broker cluster, Zookeeper (or KRaft), and significantly more setup overhead. For a manually-triggered demo with static fixtures and no concurrent load, that overhead buys nothing. In production, where events drive downstream AI workflows and losing a `candidate.created` means a candidate never gets contacted, Kafka or a similar durable bus would be the correct choice.

---

## Things I would do differently

### 1. Run `makemigrations` as part of the Dockerfile

I wrote the initial migration by hand (`0001_initial.py`) rather than generating it with Django. This works but is fragile — if the model changes, someone needs to remember to update the migration manually rather than running `manage.py makemigrations`. I would add a build-time `makemigrations` step in CI (not in the Dockerfile, where the DB path might not match) to catch drift.
