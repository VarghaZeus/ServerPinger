"""Background monitor loop.

A single daemon thread wakes every few seconds, works out which enabled targets
are due, and hands each one to a ThreadPoolExecutor so a slow or hanging target
cannot stall the rest. Started exactly once from the app factory.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from . import mailer, models, settings, updates
from .checks import run_check
from .util import parse_iso, utcnow, utcnow_iso

log = logging.getLogger(__name__)

TICK_SECONDS = 5
PRUNE_EVERY_SECONDS = 3600

_start_lock = threading.Lock()
_thread = None
_stop_event = threading.Event()

_inflight_lock = threading.Lock()
_inflight = set()


# ------------------------------------------------------------------ one check

def _claim(target_id: int) -> bool:
    with _inflight_lock:
        if target_id in _inflight:
            return False
        _inflight.add(target_id)
        return True


def _release(target_id: int) -> None:
    with _inflight_lock:
        _inflight.discard(target_id)


def perform_check(target_id: int):
    """Run one check and apply its result. Returns the CheckResult, or None."""
    target = models.get_target(target_id)
    if target is None:
        return None
    result = run_check(target)
    try:
        _apply_result(target, result)
    except Exception:  # noqa: BLE001 - never let one target kill the worker
        log.exception("Failed to apply check result for target %s", target_id)
    return result


def _apply_result(target, result) -> None:
    now = utcnow_iso()
    state = models.get_state(target["id"])
    previous_status = (state["status"] or "unknown") if state else "unknown"
    failures = (state["consecutive_failures"] or 0) if state else 0

    models.record_check(target["id"], now, result.ok, result.latency_ms, result.error)

    if result.ok:
        fields = {
            "status": "up",
            "consecutive_failures": 0,
            "last_checked_at": now,
            "last_up_at": now,
            "last_latency_ms": result.latency_ms,
            "last_error": None,
        }
        recovered = previous_status == "down"
        if recovered:
            fields["last_alert_sent_at"] = None
        models.update_state(target["id"], fields)
        if recovered:
            log.info("%s (%s) recovered", target["name"], target["host"])
            if settings.get_bool("send_recovery_emails", True):
                mailer.notify(target, models.get_state(target["id"]), "recovery",
                              down_since=state["last_down_at"])
        return

    failures += 1
    threshold = max(1, int(target["fail_threshold"] or 1))
    fields = {
        "consecutive_failures": failures,
        "last_checked_at": now,
        "last_latency_ms": result.latency_ms,
        "last_error": result.error,
    }
    newly_down = failures >= threshold and previous_status != "down"
    if newly_down:
        fields["status"] = "down"
        fields["last_down_at"] = now
        fields["last_alert_sent_at"] = now
    elif previous_status != "down":
        # Failing, but not yet past the threshold: leave the status alone.
        fields["status"] = previous_status
    else:
        fields["status"] = "down"

    models.update_state(target["id"], fields)
    fresh_state = models.get_state(target["id"])

    if newly_down:
        log.warning("%s (%s) is DOWN after %d consecutive failures: %s",
                    target["name"], target["host"], failures, result.error)
        mailer.notify(target, fresh_state, "down")
    elif previous_status == "down":
        _maybe_remind(target, fresh_state)


def _maybe_remind(target, state) -> None:
    """While a target stays down, at most one reminder per throttle window."""
    hours = settings.get_int("crash_reminder_hours", 24)
    if hours <= 0:
        return
    last_sent = parse_iso(state["last_alert_sent_at"])
    if last_sent is not None:
        elapsed_hours = (utcnow() - last_sent).total_seconds() / 3600.0
        if elapsed_hours < hours:
            return
    models.update_state(target["id"], {"last_alert_sent_at": utcnow_iso()})
    log.info("Sending down reminder for %s", target["name"])
    mailer.notify(target, models.get_state(target["id"]), "down")


def check_now(target_id: int):
    """Run a check immediately, from a request thread. Returns CheckResult|None."""
    if not _claim(target_id):
        return None
    try:
        return perform_check(target_id)
    finally:
        _release(target_id)


# ------------------------------------------------------------------- the loop

def _due_targets():
    now = utcnow()
    due = []
    for target in models.list_targets(enabled_only=True):
        last = parse_iso(target["last_checked_at"])
        if last is None:
            due.append(target["id"])
            continue
        interval = max(5, int(target["interval_seconds"] or 60))
        if (now - last).total_seconds() >= interval:
            due.append(target["id"])
    return due


def _worker(target_id: int) -> None:
    try:
        perform_check(target_id)
    except Exception:  # noqa: BLE001
        log.exception("Monitor worker failed for target %s", target_id)
    finally:
        _release(target_id)


def _loop() -> None:
    max_workers = max(1, settings.get_int("max_workers", 10))
    log.info("Monitor loop started (tick %ss, %s workers)", TICK_SECONDS, max_workers)
    executor = ThreadPoolExecutor(max_workers=max_workers,
                                  thread_name_prefix="pinger")
    last_prune = 0.0
    try:
        while not _stop_event.is_set():
            try:
                for target_id in _due_targets():
                    if _claim(target_id):
                        executor.submit(_worker, target_id)
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("Monitor tick failed")

            try:
                now = utcnow().timestamp()
                if now - last_prune >= PRUNE_EVERY_SECONDS:
                    last_prune = now
                    removed = models.prune_history(
                        settings.get_int("history_retention_days", 7))
                    if removed:
                        log.info("Pruned %d history rows", removed)
                    executor.submit(updates.maybe_check)
            except Exception:  # noqa: BLE001
                log.exception("Housekeeping failed")

            _stop_event.wait(TICK_SECONDS)
    finally:
        executor.shutdown(wait=False)
        log.info("Monitor loop stopped")


def start() -> None:
    """Start the monitor thread exactly once."""
    global _thread
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(target=_loop, name="serverpinger-monitor",
                                   daemon=True)
        _thread.start()


def stop() -> None:
    _stop_event.set()


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
