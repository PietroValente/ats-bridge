"""
Unit tests for repository.upsert_candidate.

Verifies the changed_fields logic: the core invariant that drives
whether talent_pool emits candidate.created, candidate.updated, or nothing.
"""
import os
import tempfile

import repository

_BASE = {
    "external_id": "alpha-001",
    "ats_source": "alpha",
    "first_name": "Mario",
    "last_name": "Rossi",
    "email": "mario.rossi@example.com",
    "phone": "+39 02 1234567",
    "age": 35,
    "job_external_id": "REQ-001",
    "internal_status": "new",
    "applied_at": "2026-01-15T09:00:00Z",
}


def _make_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    repository.init_db(f.name)
    return f.name


def test_insert_returns_created_true_and_empty_changed_fields():
    db = _make_db()
    try:
        pk, created, changed = repository.upsert_candidate(db, _BASE)
        assert created is True
        assert changed == []
        assert isinstance(pk, str) and pk
    finally:
        os.unlink(db)


def test_identical_upsert_is_noop():
    db = _make_db()
    try:
        pk1, _, _ = repository.upsert_candidate(db, _BASE)
        pk2, created, changed = repository.upsert_candidate(db, _BASE)
        assert pk1 == pk2
        assert created is False
        assert changed == []
    finally:
        os.unlink(db)


def test_status_change_detected_in_changed_fields():
    db = _make_db()
    try:
        repository.upsert_candidate(db, _BASE)
        updated = {**_BASE, "internal_status": "hired"}
        _, created, changed = repository.upsert_candidate(db, updated)
        assert created is False
        assert "internal_status" in changed
        assert len(changed) == 1
    finally:
        os.unlink(db)


def test_multiple_field_changes_all_detected():
    db = _make_db()
    try:
        repository.upsert_candidate(db, _BASE)
        updated = {**_BASE, "internal_status": "rejected", "phone": "+39 06 9999999"}
        _, created, changed = repository.upsert_candidate(db, updated)
        assert created is False
        assert set(changed) == {"internal_status", "phone"}
    finally:
        os.unlink(db)


def test_different_ats_source_creates_separate_record():
    db = _make_db()
    try:
        pk_alpha, created_a, _ = repository.upsert_candidate(db, _BASE)
        beta_record = {**_BASE, "ats_source": "beta"}
        pk_beta, created_b, _ = repository.upsert_candidate(db, beta_record)
        assert created_a is True
        assert created_b is True
        assert pk_alpha != pk_beta
    finally:
        os.unlink(db)
