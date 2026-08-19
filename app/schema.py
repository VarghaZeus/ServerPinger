"""Schema creation and ordered migrations.

`schema_version` lives in the settings table; migrations are a plain ordered
list of functions applied at startup. No Alembic.
"""
from __future__ import annotations

import logging

from .db import get_db, query_one

log = logging.getLogger(__name__)


def _m001_initial(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS targets (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT    NOT NULL,
            host             TEXT    NOT NULL,
            enabled          INTEGER NOT NULL DEFAULT 1,
            group_name       TEXT,
            check_type       TEXT    NOT NULL DEFAULT 'icmp',
            port             INTEGER,
            path             TEXT    DEFAULT '/',
            expect_status    INTEGER DEFAULT 200,
            verify_tls       INTEGER NOT NULL DEFAULT 1,
            interval_seconds INTEGER NOT NULL DEFAULT 60,
            timeout_seconds  INTEGER NOT NULL DEFAULT 5,
            fail_threshold   INTEGER NOT NULL DEFAULT 3,
            notes            TEXT,
            created_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS target_state (
            target_id            INTEGER PRIMARY KEY
                                 REFERENCES targets(id) ON DELETE CASCADE,
            status               TEXT    NOT NULL DEFAULT 'unknown',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_checked_at      TEXT,
            last_up_at           TEXT,
            last_down_at         TEXT,
            last_latency_ms      REAL,
            last_error           TEXT,
            last_alert_sent_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS check_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id  INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            checked_at TEXT    NOT NULL,
            ok         INTEGER NOT NULL,
            latency_ms REAL,
            error      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_history_target_time
            ON check_history(target_id, checked_at);

        CREATE TABLE IF NOT EXISTS subscribers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT    NOT NULL,
            name       TEXT,
            enabled    INTEGER NOT NULL DEFAULT 1,
            target_id  INTEGER REFERENCES targets(id) ON DELETE CASCADE,
            group_name TEXT,
            created_at TEXT
        );
        """
    )


# Ordered. Append new migrations to the end, never renumber.
MIGRATIONS = [
    ("001_initial", _m001_initial),
]


def _current_version(conn) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
    ).fetchone()
    if row is None:
        return 0
    row = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def migrate() -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    conn = get_db()
    version = _current_version(conn)
    for index, (name, func) in enumerate(MIGRATIONS, start=1):
        if index <= version:
            continue
        log.info("Applying migration %s", name)
        with conn:
            func(conn)
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(index),),
            )
        version = index
    return version


def schema_version() -> int:
    row = query_one("SELECT value FROM settings WHERE key='schema_version'")
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0
