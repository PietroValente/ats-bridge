# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Start all services (first run builds images):**
```bash
docker compose up --build
```

**Reset state completely (drops all SQLite volumes):**
```bash
docker compose down -v && docker compose up -d
```

**Reset only the sync watermark (no volume loss):**
```bash
docker compose exec push_data_manager python manage.py shell -c \
  "from db_models.models import SyncState; SyncState.objects.all().update(last_sync_at=None)"
```

**Run unit tests (no containers needed):**
```bash
docker run --rm --entrypoint="" -v ./tests:/tests ats-bridge-push_data_manager \
  sh -c "pip install pytest -q && cd /app && python -m pytest /tests/test_adapter_normalize.py /tests/test_changed_fields.py -v"
```

**Run integration tests (containers must be up):**
```bash
docker run --rm --network ats-bridge_default --entrypoint="" \
  -v ./tests:/tests ats-bridge-push_data_manager \
  sh -c "pip install pytest requests -q && python -m pytest /tests/test_integration.py -v"
```

**Smoke test:**
```bash
curl -s -X POST http://localhost:8000/api/v1/sync/alpha | python -m json.tool
# Expected: pulled=12, pushed=9, skipped=3 on first run after volume reset
```

**Inspect event_logger DB:**
```bash
docker compose exec event_logger sqlite3 /data/event_logger.db \
  "SELECT event_type, count(*) FROM processed_events GROUP BY event_type"
```

## Architecture

See `ARCHITECTURE.md` for the component diagram and design rationale. Short version:

```
curl → push_data_manager (Django :8000)
           ├── HTTP → fake_ats_alpha (FastAPI :8001)
           ├── HTTP → fake_ats_beta  (FastAPI :8002)
           └── gRPC → talent_pool (:50051)
                          └── Redis pub/sub → event_logger
```

**Proto stubs are generated at Docker build time** into each container that needs them (push_data_manager, talent_pool). No generated files are committed. Both services use root build context so their Dockerfiles can `COPY proto/`.

**Fixtures are volume-mounted** from the host into fake_ats_alpha and fake_ats_beta, so you can edit them and restart the container without rebuilding.

## Key patterns

**Adapter registry** (`push_data_manager/adapters/__init__.py`): `REGISTRY: dict[str, ATSAdapter]` maps ATS name to adapter instance. The manager has no `if/elif` on ATS source — it calls `REGISTRY.get(ats_source)` and uses the same interface for all sources. Adding a third ATS = one new file + one line in the registry.

**Normalize vs validate**: adapters raise on unparseable data (`ValueError` for bad birth_date, `KeyError` for unknown status). The manager catches these as `normalization_error`. Structural rules (email present, age ≥ 18) live only in `managers/sync_manager.py::_validate()`.

**Import rule** (strictly enforced in push_data_manager):
- `rest_views/` → imports only from `managers/`
- `managers/` → may import from `adapters/`, `db_models/`, `utils/`
- Never import upward (no model access in views, no adapter calls in views)

**Idempotency**: talent_pool SQLite has `UNIQUE(ats_source, external_id)`; event_logger has `UNIQUE(event_id)`. Safe to re-run syncs.

**changed_fields** computed in `talent_pool/repository.py`: compares each field with `str(existing[f] or "") != str(c.get(f) or "")` to handle None/int/string consistently. Only fields in `_TRACKED_FIELDS` are compared.

**Event emission rule** (in `talent_pool/servicer.py`): `created=True` → `candidate.created`; `created=False` + non-empty `changed_fields` → `candidate.updated`; no-op → no event.

**First-run window** = `now() - 7 days` (the spec default, in `sync_manager.py::_INITIAL_LOOKBACK`). Fixtures are dated within the last 24h so the initial sync pulls them all. Trade-off: the second-sync no-op assumes the demo runs after the latest fixture timestamp, kept at 02:00 UTC — see `DECISIONS.md`.

## Known issues / trade-offs

Documented in `DECISIONS.md § Known limitations`:
1. Watermark is `datetime.now()` not `max(applied_at)` — potential gap on concurrent ingestion (not reachable with static fixtures).
2. Alpha `age` is derived from `date.today()` — can emit spurious `candidate.updated` on birthday boundary.
3. Beta `age=None` collapses to `0` → skipped as `minor_candidate` instead of `normalization_error`.
