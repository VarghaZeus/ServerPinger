"""Release-tag update check.

Asks the configured git remote for its newest release tag and caches the answer.
Fails silently: an air-gapped host must keep monitoring and alerting normally.
"""
from __future__ import annotations

import logging
import re
import subprocess

from . import settings
from .paths import APP_ROOT, version as app_version
from .util import parse_iso, utcnow, utcnow_iso

log = logging.getLogger(__name__)

CHECK_INTERVAL_HOURS = 6
GIT_TIMEOUT_SECONDS = 20

_TAG_RE = re.compile(r"refs/tags/v?(\d+)\.(\d+)\.(\d+)(?:\^\{\})?$")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> None."""
    match = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", str(text or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _git_tags():
    repo_url = (settings.get("update_repo_url") or "").strip()
    command = ["git", "ls-remote", "--tags", "--refs"]
    if repo_url:
        command.append(repo_url)
    else:
        command = ["git", "-C", str(APP_ROOT), "ls-remote", "--tags", "--refs", "origin"]
    completed = subprocess.run(
        command,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=GIT_TIMEOUT_SECONDS,
        creationflags=_NO_WINDOW,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or "git ls-remote exited %d" % completed.returncode)
    output = (completed.stdout or b"").decode("utf-8", errors="replace")
    versions = []
    for line in output.splitlines():
        match = _TAG_RE.search(line.strip())
        if match:
            versions.append(tuple(int(part) for part in match.groups()))
    return versions


def check() -> bool:
    """Run the remote check now. Returns True on success. Never raises."""
    if not settings.get_bool("update_check_enabled", True):
        return False
    try:
        versions = _git_tags()
    except Exception as exc:  # noqa: BLE001 - offline is a normal condition here
        log.debug("Update check failed: %s", exc)
        settings.set_many({
            "update_last_checked_at": utcnow_iso(),
            "update_last_status": "failed",
        })
        return False

    latest = max(versions) if versions else None
    settings.set_many({
        "update_last_checked_at": utcnow_iso(),
        "update_last_status": "ok",
        "update_latest_version": ".".join(str(part) for part in latest) if latest else "",
    })
    return True


def maybe_check() -> None:
    """Check if the cached result is older than the interval."""
    if not settings.get_bool("update_check_enabled", True):
        return
    last = parse_iso(settings.get("update_last_checked_at"))
    if last is not None:
        elapsed_hours = (utcnow() - last).total_seconds() / 3600.0
        if elapsed_hours < CHECK_INTERVAL_HOURS:
            return
    check()


def status() -> dict:
    """Everything the footer and /api/version need."""
    current = app_version()
    info = {
        "version": current,
        "update_check_enabled": settings.get_bool("update_check_enabled", True),
        "latest_version": settings.get("update_latest_version") or None,
        "last_checked_at": settings.get("update_last_checked_at") or None,
        "last_status": settings.get("update_last_status") or None,
        "update_available": False,
        "text": "update check off",
    }
    if not info["update_check_enabled"]:
        return info

    if info["last_status"] != "ok":
        info["text"] = "update check failed"
        return info

    current_tuple = parse_version(current)
    latest_tuple = parse_version(info["latest_version"])
    if latest_tuple and current_tuple and latest_tuple > current_tuple:
        info["update_available"] = True
        info["text"] = "update available: v%s" % info["latest_version"]
        return info

    checked = parse_iso(info["last_checked_at"])
    if checked is None:
        info["text"] = "up to date"
    else:
        minutes = int((utcnow() - checked).total_seconds() // 60)
        info["text"] = "up to date (checked %d min ago)" % max(0, minutes)
    return info
