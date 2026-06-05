# Architecture

## Components

```
curl → push_data_manager (Django, :8000)
           │
           ├─── HTTP GET ──→ fake_ats_alpha (FastAPI, :8001)
           ├─── HTTP GET ──→ fake_ats_beta  (FastAPI, :8002)
           │
           └─── gRPC ──→ talent_pool (Python, :50051)
                              │
                              └─── PUBLISH ──→ Redis pub/sub ──→ event_logger
```

## Multi-ATS

Each ATS has different field names, status vocabulary, and timestamp format. The solution is a registry of adapters:

```python
REGISTRY: dict[str, ATSAdapter] = {
    "alpha": AlphaAdapter(),
    "beta":  BetaAdapter(),
}
```

The manager looks up the adapter by name and calls the same two methods every time: `fetch_applications(since)` and `normalize(raw)`. Adding a third ATS is one file plus one line in the registry.

Adapters raise on data they can't parse (invalid birth_date, unknown status). The manager catches those and records them as `normalization_error`. Structural validation (email present, age ≥ 18) lives in the manager, it's a business rule that applies the same way to every source.

## gRPC contract

Defined in `/proto/klaaryo.proto`. Stubs are generated at Docker build time; nothing generated is committed.

```
UpsertCandidate(NormalizedCandidate) → UpsertResult
  keyed on (ats_source, external_id)
  returns: candidate_pk, created: bool, changed_fields: []string

GetCandidate(GetCandidateRequest) → Candidate
ListCandidates(ListCandidatesRequest) → ListCandidatesResponse
```

## Events

Published on Redis pub/sub after every non-no-op upsert:

```json
{
  "event_id":       "uuid-v4",
  "event_type":     "candidate.created" | "candidate.updated",
  "occurred_at":    "2026-06-04T12:00:00Z",
  "candidate_pk":   "550e8400-e29b-41d4-a716-446655440000",
  "ats_source":     "alpha",
  "external_id":    "alpha-001",
  "changed_fields": ["internal_status"]
}
```

`created=True` → `candidate.created`. `created=False` + non-empty `changed_fields` → `candidate.updated`. No-op → no event.
