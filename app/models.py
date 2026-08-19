"""Queries shared by the request threads and the monitor thread."""
from __future__ import annotations

from . import db
from .util import since_iso, utcnow_iso

TARGET_FIELDS = (
    "name", "host", "enabled", "group_name", "check_type", "port", "path",
    "expect_status", "verify_tls", "interval_seconds", "timeout_seconds",
    "fail_threshold", "notes",
)


# --------------------------------------------------------------------- targets

def list_targets(enabled_only: bool = False):
    sql = (
        "SELECT t.*, s.status, s.consecutive_failures, s.last_checked_at, "
        "       s.last_up_at, s.last_down_at, s.last_latency_ms, s.last_error, "
        "       s.last_alert_sent_at "
        "FROM targets t LEFT JOIN target_state s ON s.target_id = t.id "
    )
    if enabled_only:
        sql += "WHERE t.enabled = 1 "
    sql += "ORDER BY COALESCE(NULLIF(t.group_name, ''), 'zzzz') COLLATE NOCASE, t.name COLLATE NOCASE"
    return db.query(sql)


def get_target(target_id: int):
    return db.query_one(
        "SELECT t.*, s.status, s.consecutive_failures, s.last_checked_at, "
        "       s.last_up_at, s.last_down_at, s.last_latency_ms, s.last_error, "
        "       s.last_alert_sent_at "
        "FROM targets t LEFT JOIN target_state s ON s.target_id = t.id "
        "WHERE t.id = ?",
        (target_id,),
    )


def create_target(values: dict) -> int:
    columns = ", ".join(TARGET_FIELDS) + ", created_at"
    placeholders = ", ".join(["?"] * (len(TARGET_FIELDS) + 1))
    args = [values.get(field) for field in TARGET_FIELDS] + [utcnow_iso()]
    target_id = db.execute(
        "INSERT INTO targets (%s) VALUES (%s)" % (columns, placeholders), args
    )
    db.execute(
        "INSERT INTO target_state (target_id, status) VALUES (?, 'unknown')",
        (target_id,),
    )
    return target_id


def update_target(target_id: int, values: dict) -> None:
    assignments = ", ".join("%s = ?" % field for field in TARGET_FIELDS)
    args = [values.get(field) for field in TARGET_FIELDS] + [target_id]
    db.execute("UPDATE targets SET %s WHERE id = ?" % assignments, args)
    db.execute(
        "INSERT OR IGNORE INTO target_state (target_id, status) VALUES (?, 'unknown')",
        (target_id,),
    )


def clone_target(target_id: int):
    """Duplicate a target, disabled, so the copy can be re-pointed before it runs."""
    source = get_target(target_id)
    if source is None:
        return None
    values = {field: source[field] for field in TARGET_FIELDS}
    values["name"] = _copy_name(source["name"])
    values["enabled"] = 0
    return create_target(values)


def _copy_name(name: str) -> str:
    existing = {row["name"] for row in db.query("SELECT name FROM targets")}
    candidate = "%s (copy)" % name
    counter = 2
    while candidate in existing:
        candidate = "%s (copy %d)" % (name, counter)
        counter += 1
    return candidate[:120]


def delete_target(target_id: int) -> None:
    db.execute("DELETE FROM targets WHERE id = ?", (target_id,))


def set_target_enabled(target_id: int, enabled: bool) -> None:
    db.execute("UPDATE targets SET enabled = ? WHERE id = ?", (1 if enabled else 0, target_id))
    if not enabled:
        # A disabled target is not "up" or "down", it is simply not being watched.
        db.execute(
            "UPDATE target_state SET status = 'unknown', consecutive_failures = 0 "
            "WHERE target_id = ?",
            (target_id,),
        )


def count_targets() -> int:
    return int(db.scalar("SELECT COUNT(*) FROM targets", (), 0))


# ----------------------------------------------------------------------- state

def get_state(target_id: int):
    row = db.query_one("SELECT * FROM target_state WHERE target_id = ?", (target_id,))
    if row is None:
        db.execute(
            "INSERT OR IGNORE INTO target_state (target_id, status) VALUES (?, 'unknown')",
            (target_id,),
        )
        row = db.query_one("SELECT * FROM target_state WHERE target_id = ?", (target_id,))
    return row


def update_state(target_id: int, values: dict) -> None:
    if not values:
        return
    assignments = ", ".join("%s = ?" % key for key in values)
    args = list(values.values()) + [target_id]
    db.execute("UPDATE target_state SET %s WHERE target_id = ?" % assignments, args)


def status_counts() -> dict:
    counts = {"up": 0, "down": 0, "unknown": 0}
    rows = db.query(
        "SELECT COALESCE(s.status, 'unknown') AS status, COUNT(*) AS n "
        "FROM targets t LEFT JOIN target_state s ON s.target_id = t.id "
        "GROUP BY COALESCE(s.status, 'unknown')"
    )
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] = row["n"]
    return counts


# --------------------------------------------------------------------- history

def record_check(target_id: int, checked_at: str, ok: bool, latency_ms, error) -> None:
    db.execute(
        "INSERT INTO check_history (target_id, checked_at, ok, latency_ms, error) "
        "VALUES (?, ?, ?, ?, ?)",
        (target_id, checked_at, 1 if ok else 0, latency_ms, error),
    )


def recent_history(target_id: int, limit: int = 100):
    return db.query(
        "SELECT * FROM check_history WHERE target_id = ? "
        "ORDER BY checked_at DESC, id DESC LIMIT ?",
        (target_id, limit),
    )


def uptime_percent(target_id: int, hours: float = 24.0):
    row = db.query_one(
        "SELECT COUNT(*) AS total, COALESCE(SUM(ok), 0) AS good FROM check_history "
        "WHERE target_id = ? AND checked_at >= ?",
        (target_id, since_iso(hours)),
    )
    if row is None or not row["total"]:
        return None
    return round(100.0 * row["good"] / row["total"], 2)


def prune_history(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = since_iso(retention_days * 24)
    conn = db.get_db()
    with conn:
        cur = conn.execute("DELETE FROM check_history WHERE checked_at < ?", (cutoff,))
    return cur.rowcount or 0


# ----------------------------------------------------------------- subscribers

def list_subscribers():
    return db.query(
        "SELECT s.*, t.name AS target_name FROM subscribers s "
        "LEFT JOIN targets t ON t.id = s.target_id "
        "ORDER BY s.email COLLATE NOCASE"
    )


def get_subscriber(subscriber_id: int):
    return db.query_one("SELECT * FROM subscribers WHERE id = ?", (subscriber_id,))


def create_subscriber(values: dict) -> int:
    return db.execute(
        "INSERT INTO subscribers (email, name, enabled, target_id, group_name, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (values.get("email"), values.get("name"), values.get("enabled"),
         values.get("target_id"), values.get("group_name"), utcnow_iso()),
    )


def update_subscriber(subscriber_id: int, values: dict) -> None:
    db.execute(
        "UPDATE subscribers SET email = ?, name = ?, enabled = ?, target_id = ?, "
        "group_name = ? WHERE id = ?",
        (values.get("email"), values.get("name"), values.get("enabled"),
         values.get("target_id"), values.get("group_name"), subscriber_id),
    )


def delete_subscriber(subscriber_id: int) -> None:
    db.execute("DELETE FROM subscribers WHERE id = ?", (subscriber_id,))


def set_subscriber_enabled(subscriber_id: int, enabled: bool) -> None:
    db.execute("UPDATE subscribers SET enabled = ? WHERE id = ?",
               (1 if enabled else 0, subscriber_id))


def recipients_for_target(target) -> list:
    """Enabled subscribers scoped to this target, its group, or to everything."""
    group_name = (target["group_name"] or "").strip()
    rows = db.query(
        "SELECT * FROM subscribers WHERE enabled = 1 AND ("
        "  target_id = ?"
        "  OR (target_id IS NULL AND (group_name IS NULL OR group_name = ''))"
        "  OR (target_id IS NULL AND group_name = ? AND ? <> '')"
        ") ORDER BY email COLLATE NOCASE",
        (target["id"], group_name, group_name),
    )
    seen = set()
    out = []
    for row in rows:
        email = (row["email"] or "").strip()
        key = email.lower()
        if email and key not in seen:
            seen.add(key)
            out.append(row)
    return out


def known_groups() -> list:
    rows = db.query(
        "SELECT DISTINCT group_name FROM targets "
        "WHERE group_name IS NOT NULL AND group_name <> '' "
        "ORDER BY group_name COLLATE NOCASE"
    )
    return [row["group_name"] for row in rows]
