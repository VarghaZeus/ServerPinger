"""Key/value settings with generic defaults.

Ships unconfigured on purpose: empty SMTP host, port 25, no security, disabled.
A fresh install must not pretend to be configured.
"""
from __future__ import annotations

from .db import execute, query, query_one

DEFAULTS = {
    # --- email ---------------------------------------------------------
    "smtp_mode": "internal_relay",      # internal_relay | smtp_auth
    "smtp_host": "",
    "smtp_port": "25",
    "smtp_security": "none",            # none | starttls | ssl
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from": "",
    "email_enabled": "0",
    "crash_reminder_hours": "24",
    "send_recovery_emails": "1",
    "last_email_error": "",
    "last_email_error_at": "",
    "last_email_sent_at": "",
    # --- monitoring ----------------------------------------------------
    "history_retention_days": "7",
    "max_workers": "10",
    # --- updates -------------------------------------------------------
    "update_check_enabled": "1",
    "update_repo_url": "",
    "update_last_checked_at": "",
    "update_latest_version": "",
    "update_last_status": "",           # ok | failed | ""
    "update_last_error": "",
    # --- internal ------------------------------------------------------
    "secret_key": "",
    "schema_version": "0",
}

SMTP_MODES = ("internal_relay", "smtp_auth")
SMTP_SECURITY = ("none", "starttls", "ssl")


def get(key: str, default=None) -> str:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    if row is None or row[0] is None:
        return DEFAULTS.get(key, "") if default is None else default
    return row[0]


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(str(get(key)).strip())
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    value = str(get(key)).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def set_value(key: str, value) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, "" if value is None else str(value)),
    )


def set_many(mapping) -> None:
    for key, value in mapping.items():
        set_value(key, value)


def all_settings() -> dict:
    values = dict(DEFAULTS)
    for row in query("SELECT key, value FROM settings"):
        values[row["key"]] = "" if row["value"] is None else row["value"]
    return values


def ensure_defaults() -> None:
    """Insert any default that has never been written. Never overwrites."""
    existing = {row["key"] for row in query("SELECT key FROM settings")}
    for key, value in DEFAULTS.items():
        if key not in existing:
            set_value(key, value)
