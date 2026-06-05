import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import redis

DB_PATH = os.environ.get("DB_PATH", "/data/event_logger.db")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id     TEXT    NOT NULL UNIQUE,
                event_type   TEXT    NOT NULL,
                candidate_pk TEXT,
                ats_source   TEXT,
                received_at  TEXT    NOT NULL,
                payload_json TEXT    NOT NULL
            )
        """)


def handle(raw: bytes) -> None:
    payload = json.loads(raw)
    event_id = payload["event_id"]
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO processed_events "
                "(event_id, event_type, candidate_pk, ats_source, received_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    payload["event_type"],
                    payload.get("candidate_pk"),
                    payload.get("ats_source"),
                    datetime.now(timezone.utc).isoformat(),
                    raw.decode(),
                ),
            )
        print(f"[PROCESSED] event_id={event_id} type={payload['event_type']}", flush=True)
    except sqlite3.IntegrityError:
        print(f"[SKIPPED] event_id={event_id} duplicate", flush=True)


def connect_redis() -> redis.Redis:
    while True:
        try:
            client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
            client.ping()
            return client
        except redis.ConnectionError:
            print("Waiting for Redis...", flush=True)
            time.sleep(2)


def main() -> None:
    init_db()
    client = connect_redis()
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe("candidate.created", "candidate.updated")
    print("event_logger subscribed to candidate.created, candidate.updated", flush=True)
    for message in pubsub.listen():
        if message["type"] == "message":
            handle(message["data"])


if __name__ == "__main__":
    main()
