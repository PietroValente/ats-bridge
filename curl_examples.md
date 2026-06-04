# End-to-end demo

All commands assume `docker compose up --build` has completed and services are ready.

---

## 1. First sync of Alpha

```bash
curl -s -X POST http://localhost:8000/api/v1/sync/alpha | python -m json.tool
```

Expected response:
```json
{
  "pulled": 12,
  "pushed": 9,
  "skipped": 3,
  "skipped_reasons": {
    "minor_candidate": 1,
    "missing_email": 1,
    "normalization_error": 1
  }
}
```

## 2. Check sync status

```bash
curl -s http://localhost:8000/api/v1/sync/alpha/status | python -m json.tool
```

Expected:
```json
{
  "last_sync_at": "2026-06-04T...",
  "total_pushed": 9,
  "total_skipped": 3
}
```

## 3. Verify candidate.created events in event_logger

The event_logger image is python:3.12-slim (no sqlite3 CLI). Use Python:

```bash
docker compose exec event_logger python -c "
import sqlite3
conn = sqlite3.connect('/data/event_logger.db')
for row in conn.execute('SELECT event_type, ats_source, count(*) FROM processed_events GROUP BY event_type, ats_source'):
    print(row)
"
```

Expected: `('candidate.created', 'alpha', 9)`

## 4. First sync of Beta

```bash
curl -s -X POST http://localhost:8000/api/v1/sync/beta | python -m json.tool
```

Expected: pulled=12, pushed=9, skipped=3 (minor + noemail + badstage)

## 5. Second sync of Alpha — must be a no-op

```bash
curl -s -X POST http://localhost:8000/api/v1/sync/alpha | python -m json.tool
```

Expected:
```json
{
  "pulled": 0,
  "pushed": 0,
  "skipped": 0,
  "skipped_reasons": {}
}
```

No events emitted (verify by checking that event count in event_logger has not changed).

---

## 6. Demonstrate candidate.updated

### a) Modify a fixture on the host

Open `fake_ats_alpha/fixtures/applications.json` and change `alpha-001`'s
`application_status` from `"NEW"` to `"HIRED"`.

### b) Restart fake_ats_alpha so it reloads the fixture

```bash
docker compose restart fake_ats_alpha
```

### c) Reset SyncState so since goes back to the initial date

```bash
docker compose exec push_data_manager python manage.py shell -c \
  "from db_models.models import SyncState; SyncState.objects.filter(ats_source='alpha').update(last_sync_at=None)"
```

### d) Sync Alpha again

```bash
curl -s -X POST http://localhost:8000/api/v1/sync/alpha | python -m json.tool
```

pushed=9, skipped=3 (same counts — no new records, but alpha-001's status changed).

### e) Verify candidate.updated event with changed_fields

```bash
docker compose exec event_logger python -c "
import sqlite3
conn = sqlite3.connect('/data/event_logger.db')
for row in conn.execute('SELECT event_type, payload_json FROM processed_events WHERE event_type = ?', ('candidate.updated',)):
    print(row)
"
```

The `payload_json` field should contain `"changed_fields": ["internal_status"]`.

---

## 7. Verify no duplicates in talent_pool

```bash
docker compose exec talent_pool python -c "
import sqlite3
conn = sqlite3.connect('/data/talent_pool.db')
for row in conn.execute('SELECT ats_source, count(*) FROM candidates GROUP BY ats_source'):
    print(row)
"
```

Expected: `('alpha', 9)` and `('beta', 9)` — no duplicates regardless of how many times syncs run.

---

## Useful one-liners

```bash
# List all candidates in talent_pool
docker compose exec talent_pool python -c "
import sqlite3
conn = sqlite3.connect('/data/talent_pool.db')
for row in conn.execute('SELECT pk, ats_source, external_id, internal_status FROM candidates'):
    print(row)
"

# Count processed events by type
docker compose exec event_logger python -c "
import sqlite3
conn = sqlite3.connect('/data/event_logger.db')
for row in conn.execute('SELECT event_type, count(*) FROM processed_events GROUP BY event_type'):
    print(row)
"

# Check Alpha ATS directly (7 days back from 2026-06-04)
curl -s "http://localhost:8001/api/applications?since=2026-05-28T00:00:00Z" | python -m json.tool

# Check Beta ATS directly (unix ts for 2026-05-28, ~7 days back)
curl -s "http://localhost:8002/v2/candidates?updated_after=1779926400" | python -m json.tool
```
