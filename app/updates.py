"""Release-tag update check.

Asks the configured git remote for its newest release tag and caches the answer.
Fails silently: an air-gapped host must keep monitoring and alerting normally.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess

from . import settings
from .paths import APP_ROOT, version as app_version
from .util import parse_iso, utcnow, utcnow_iso

log = logging.getLogger(__name__)

CHECK_INTERVAL_HOURS = 6
# A failed check retries sooner than the happy path: the usual cause is that the
# host was offline, or the remote did not exist yet, when the app started.
RETRY_AFTER_MINUTES = 15
GIT_TIMEOUT_SECONDS = 20

# Case-insensitive: release tags get pushed as v1.2.3 and V1.2.3 alike.
_TAG_RE = re.compile(r"refs/tags/v?(\d+)\.(\d+)\.(\d+)(?:\^\{\})?$", re.IGNORECASE)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3); anything unparseable -> None."""
    match = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", str(text or ""), re.IGNORECASE)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _git_env():
    """Make git fail fast instead of blocking on a credential or host-key prompt.

    A service account has no console, so an interactive prompt would just hang
    until the subprocess timeout. Inherits the real environment so PATH, HOME
    and any ~/.ssh/config host aliases still apply.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env.setdefault(
        "GIT_SSH_COMMAND",
        "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10",
    )
    return env


def _origin_url():
    """The 'origin' URL of the checkout we are running from, or None."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(APP_ROOT), "remote", "get-url", "origin"],
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            creationflags=_NO_WINDOW,
            env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or b"").decode("utf-8", errors="replace").strip() or None


def _git_tags():
    repo_url = (settings.get("update_repo_url") or "").strip()
    if not repo_url:
        # Fall back to the checkout's own remote, but say something useful when
        # this copy was deployed by copying files rather than by cloning.
        repo_url = _origin_url()
        if not repo_url:
            raise RuntimeError(
                "no update repository configured: %s is not a git checkout with an "
                "'origin' remote. Set the Update repository URL on the Email "
                "settings page (for example "
                "https://github.com/<owner>/<repo>.git)." % APP_ROOT
            )
    command = ["git", "ls-remote", "--tags", "--refs", repo_url]
    completed = subprocess.run(
        command,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=GIT_TIMEOUT_SECONDS,
        creationflags=_NO_WINDOW,
        env=_git_env(),
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
    except subprocess.TimeoutExpired:
        log.debug("Update check timed out")
        settings.set_many({
            "update_last_checked_at": utcnow_iso(),
            "update_last_status": "failed",
            "update_last_error": "git ls-remote timed out after %ds (no response from the "
                                 "remote, or it wanted credentials)" % GIT_TIMEOUT_SECONDS,
        })
        return False
    except Exception as exc:  # noqa: BLE001 - offline is a normal condition here
        log.debug("Update check failed: %s", exc)
        settings.set_many({
            "update_last_checked_at": utcnow_iso(),
            "update_last_status": "failed",
            "update_last_error": str(exc)[:500],
        })
        return False

    latest = max(versions) if versions else None
    settings.set_many({
        "update_last_checked_at": utcnow_iso(),
        "update_last_status": "ok",
        "update_last_error": "",
        "update_latest_version": ".".join(str(part) for part in latest) if latest else "",
    })
    return True


def maybe_check() -> None:
    """Check if the cached result is older than the interval."""
    if not settings.get_bool("update_check_enabled", True):
        return
    last = parse_iso(settings.get("update_last_checked_at"))
    if last is not None:
        elapsed_minutes = (utcnow() - last).total_seconds() / 60.0
        if settings.get("update_last_status") == "failed":
            if elapsed_minutes < RETRY_AFTER_MINUTES:
                return
        elif elapsed_minutes < CHECK_INTERVAL_HOURS * 60:
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
        "last_error": settings.get("update_last_error") or None,
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
