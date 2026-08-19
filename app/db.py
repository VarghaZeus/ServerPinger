"""Thin sqlite3 helper.

The monitor thread, its worker pool and every request thread touch the database,
so each thread gets its own connection instead of sharing one with
check_same_thread=False. WAL + a generous busy timeout handles the concurrency.
"""
from __future__ import annotations

import sqlite3
import threading

from .paths import db_path

_local = threading.local()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def close_db() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


def query(sql: str, args=()):
    return get_db().execute(sql, args).fetchall()


def query_one(sql: str, args=()):
    return get_db().execute(sql, args).fetchone()


def execute(sql: str, args=()) -> int:
    conn = get_db()
    with conn:
        cur = conn.execute(sql, args)
    return cur.lastrowid


def executemany(sql: str, seq) -> None:
    conn = get_db()
    with conn:
        conn.executemany(sql, seq)


def scalar(sql: str, args=(), default=None):
    row = query_one(sql, args)
    if row is None:
        return default
    value = row[0]
    return default if value is None else value
