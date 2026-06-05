# ATS Bridge

Five-component integration layer that pulls candidates from two fake ATS services, normalises them into an internal schema, persists them via gRPC, and emits events on Redis pub/sub.

## Prerequisites

- Docker Desktop (with Compose V2)

That is the only requirement. No local Python setup needed.

## Start everything

```bash
docker compose up --build
```

The first build takes ~2–3 minutes (pip installs + grpc stub generation). On subsequent runs without code changes, `docker compose up` starts in seconds.

Wait ~15 seconds after the build for all services to be ready, then proceed.

## Quick smoke test

```bash
# Sync Alpha — should return pulled ≥ 12, pushed = 9, skipped = 3
curl -s -X POST http://localhost:8000/api/v1/sync/alpha | python -m json.tool

# Sync Beta — same shape
curl -s -X POST http://localhost:8000/api/v1/sync/beta | python -m json.tool

# Second sync Alpha — must be a no-op: pulled = 0, pushed = 0
curl -s -X POST http://localhost:8000/api/v1/sync/alpha | python -m json.tool

# Check events logged by event_logger (slim image has no sqlite3 CLI, use Python)
docker compose exec event_logger python -c "
import sqlite3
conn = sqlite3.connect('/data/event_logger.db')
for row in conn.execute('SELECT event_type, count(*) FROM processed_events GROUP BY event_type'):
    print(row)
"
```

See [`curl_examples.md`](curl_examples.md) for the full end-to-end demo including the `candidate.updated` flow.

## Ports

| Service            | Port  | Protocol |
|--------------------|-------|----------|
| push_data_manager  | 8000  | HTTP     |
| fake_ats_alpha     | 8001  | HTTP     |
| fake_ats_beta      | 8002  | HTTP     |
| talent_pool        | 50051 | gRPC     |
| Redis              | 6379  | Redis    |

## Run unit tests (no containers needed)

```bash
pip install -r tests/requirements.txt
pytest tests/test_adapter_normalize.py tests/test_changed_fields.py -v
```

## Run integration tests (containers must be up)

```bash
pytest tests/test_integration.py -v
```

## Stopping and resetting

```bash
docker compose down -v   # removes containers AND named volumes (resets all DBs)
docker compose down      # removes containers, keeps volumes (data persists)
```
