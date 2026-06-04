import sqlite3
from typing import Optional

_TRACKED_FIELDS = [
    "first_name",
    "last_name",
    "email",
    "phone",
    "age",
    "job_external_id",
    "internal_status",
    "applied_at",
]


def init_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                pk              INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id     TEXT    NOT NULL,
                ats_source      TEXT    NOT NULL,
                first_name      TEXT,
                last_name       TEXT,
                email           TEXT,
                phone           TEXT,
                age             INTEGER,
                job_external_id TEXT,
                internal_status TEXT,
                applied_at      TEXT,
                UNIQUE(ats_source, external_id)
            )
        """)


def upsert_candidate(db_path: str, c: dict) -> tuple[int, bool, list[str]]:
    """
    Insert or update a candidate keyed on (ats_source, external_id).
    Returns (pk, created, changed_fields).
    changed_fields is empty on insert and on a no-op update.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT * FROM candidates WHERE ats_source = ? AND external_id = ?",
            (c["ats_source"], c["external_id"]),
        ).fetchone()

        if existing is None:
            cur = conn.execute(
                "INSERT INTO candidates "
                "(external_id, ats_source, first_name, last_name, email, phone, "
                "age, job_external_id, internal_status, applied_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c["external_id"],
                    c["ats_source"],
                    c["first_name"],
                    c["last_name"],
                    c["email"],
                    c["phone"],
                    c["age"],
                    c["job_external_id"],
                    c["internal_status"],
                    c["applied_at"],
                ),
            )
            return cur.lastrowid, True, []

        # str() normalises None vs "" vs int so comparisons are consistent
        changed = [
            f for f in _TRACKED_FIELDS
            if str(existing[f] or "") != str(c.get(f) or "")
        ]
        if changed:
            set_clause = ", ".join(f"{f} = ?" for f in _TRACKED_FIELDS)
            conn.execute(
                f"UPDATE candidates SET {set_clause} WHERE pk = ?",
                [c.get(f) for f in _TRACKED_FIELDS] + [existing["pk"]],
            )
        return existing["pk"], False, changed


def get_candidate(db_path: str, pk: int) -> Optional[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM candidates WHERE pk = ?", (pk,)
        ).fetchone()


def list_candidates(
    db_path: str, ats_source: Optional[str] = None
) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if ats_source:
            return conn.execute(
                "SELECT * FROM candidates WHERE ats_source = ?", (ats_source,)
            ).fetchall()
        return conn.execute("SELECT * FROM candidates").fetchall()
