"""Filesystem locations. Everything goes through pathlib so UNC paths work."""
from __future__ import annotations

import os
import platform
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

_cached_data_dir = None


def _candidate_data_dir() -> Path:
    override = os.environ.get("SERVERPINGER_DATA", "").strip()
    if override:
        return Path(override)
    if platform.system() == "Windows":
        program_data = os.environ.get("PROGRAMDATA", "").strip()
        if program_data:
            return Path(program_data) / "ServerPinger"
        return APP_ROOT / "data"
    return Path.home() / ".local" / "share" / "serverpinger"


def data_dir() -> Path:
    """SERVERPINGER_DATA -> platform default -> data/ next to the app."""
    global _cached_data_dir
    if _cached_data_dir is not None:
        return _cached_data_dir
    candidate = _candidate_data_dir()
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError:
        candidate = APP_ROOT / "data"
        candidate.mkdir(parents=True, exist_ok=True)
    _cached_data_dir = candidate
    return candidate


def db_path() -> Path:
    return data_dir() / "serverpinger.db"


def log_path() -> Path:
    return data_dir() / "serverpinger.log"


def version() -> str:
    try:
        return (APP_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"
