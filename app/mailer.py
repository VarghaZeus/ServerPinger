"""SMTP delivery and alert composition.

Sending must never block or kill the monitor loop: every failure is caught,
logged, and persisted to settings for display in the UI.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from . import models, settings
from .util import human_duration, parse_iso, reporting_instance, utcnow, utcnow_iso

log = logging.getLogger(__name__)

SMTP_CONNECT_TIMEOUT = 20.0


def _from_address() -> str:
    configured = (settings.get("smtp_from") or "").strip()
    if configured:
        return configured
    return formataddr(("ServerPinger", "serverpinger@%s" % reporting_instance()))


def _record_error(message) -> None:
    settings.set_many({
        "last_email_error": message or "",
        "last_email_error_at": utcnow_iso() if message else "",
    })


def send_email(recipients, subject: str, text_body: str, html_body=None,
               force: bool = False) -> tuple:
    """Send one message. Returns (ok, error_text). Never raises.

    `force` bypasses the "email enabled" master switch so the settings page can
    test the server before alerting is turned on.
    """
    recipients = [r for r in (recipients or []) if r and r.strip()]
    if not recipients:
        return False, "no recipients"
    if not force and not settings.get_bool("email_enabled"):
        return False, "email sending is disabled in settings"

    host = (settings.get("smtp_host") or "").strip()
    if not host:
        error = "no SMTP host configured"
        _record_error(error)
        return False, error

    port = settings.get_int("smtp_port", 25)
    security = (settings.get("smtp_security") or "none").strip().lower()
    mode = (settings.get("smtp_mode") or "internal_relay").strip().lower()
    username = (settings.get("smtp_username") or "").strip()
    password = settings.get("smtp_password") or ""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _from_address()
    message["To"] = ", ".join(recipients)
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    sender = parseaddr(message["From"])[1] or "serverpinger@localhost"

    server = None
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=SMTP_CONNECT_TIMEOUT)
        else:
            server = smtplib.SMTP(host, port, timeout=SMTP_CONNECT_TIMEOUT)
            server.ehlo()
            if security == "starttls":
                server.starttls()
                server.ehlo()
        # Internal-relay mode makes no login() call at all.
        if mode == "smtp_auth" and username:
            server.login(username, password)
        server.send_message(message, from_addr=sender, to_addrs=recipients)
    except Exception as exc:  # noqa: BLE001 - surface the real SMTP text, never crash
        error = "%s: %s" % (type(exc).__name__, exc)
        log.warning("Email send failed (%s -> %s): %s", host, recipients, error)
        _record_error(error)
        return False, error
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001 - closing is best effort
                try:
                    server.close()
                except Exception:  # noqa: BLE001
                    pass

    settings.set_many({
        "last_email_error": "",
        "last_email_error_at": "",
        "last_email_sent_at": utcnow_iso(),
    })
    log.info("Sent %r to %s", subject, ", ".join(recipients))
    return True, None


# ------------------------------------------------------------------- content

def _rows_html(rows) -> str:
    cells = []
    for label, value in rows:
        cells.append(
            '<tr><td style="padding:4px 12px 4px 0;color:#8b93a7;">%s</td>'
            '<td style="padding:4px 0;color:#e6e9ef;font-family:monospace;">%s</td></tr>'
            % (_escape(label), _escape(value))
        )
    return "".join(cells)


def _escape(value) -> str:
    text = "" if value is None else str(value)
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def _wrap_html(title: str, colour: str, rows) -> str:
    return (
        '<html><body style="background:#101318;color:#e6e9ef;'
        'font-family:Segoe UI,Helvetica,Arial,sans-serif;padding:16px;">'
        '<h2 style="color:%s;margin:0 0 12px 0;">%s</h2>'
        '<table cellspacing="0" cellpadding="0">%s</table>'
        '<p style="color:#8b93a7;font-size:12px;margin-top:16px;">'
        'Sent by ServerPinger.</p></body></html>'
        % (colour, _escape(title), _rows_html(rows))
    )


def _target_rows(target):
    return [
        ("Target", target["name"]),
        ("Host", target["host"]),
        ("Check type", (target["check_type"] or "icmp").upper()),
        ("Group", target["group_name"] or "(none)"),
    ]


def _text_from_rows(rows) -> str:
    width = max(len(label) for label, _ in rows)
    return "\n".join("%-*s : %s" % (width, label, "" if value is None else value)
                     for label, value in rows)


def compose_down(target, state) -> tuple:
    instance = reporting_instance()
    first_down = state["last_down_at"] or utcnow_iso()
    rows = _target_rows(target) + [
        ("Status", "DOWN"),
        ("First seen down", first_down),
        ("Consecutive failures", state["consecutive_failures"]),
        ("Last error", state["last_error"] or "(none recorded)"),
        ("Reporting instance", instance),
    ]
    subject = "[ServerPinger] DOWN: %s (%s) - seen by %s" % (
        target["name"], target["host"], instance)
    text = (
        "%s is DOWN.\n\n%s\n\n"
        "Times are UTC.\n" % (target["name"], _text_from_rows(rows))
    )
    return subject, text, _wrap_html("DOWN: %s" % target["name"], "#ff6b6b", rows)


def compose_recovery(target, state, down_since) -> tuple:
    instance = reporting_instance()
    now = utcnow()
    started = parse_iso(down_since)
    downtime = human_duration((now - started).total_seconds()) if started else "unknown"
    rows = _target_rows(target) + [
        ("Status", "UP"),
        ("Was down since", down_since or "unknown"),
        ("Recovered at", utcnow_iso()),
        ("Total downtime", downtime),
        ("Last latency", "%s ms" % state["last_latency_ms"]
         if state["last_latency_ms"] is not None else "n/a"),
        ("Reporting instance", instance),
    ]
    subject = "[ServerPinger] RECOVERED: %s (%s) - seen by %s" % (
        target["name"], target["host"], instance)
    text = (
        "%s has RECOVERED after %s.\n\n%s\n\n"
        "Times are UTC.\n" % (target["name"], downtime, _text_from_rows(rows))
    )
    return subject, text, _wrap_html("RECOVERED: %s" % target["name"], "#4ade80", rows)


def notify(target, state, kind: str, down_since=None) -> None:
    """Compose and send an alert for a target. Swallows every failure."""
    try:
        if not settings.get_bool("email_enabled"):
            return
        recipients = [row["email"].strip() for row in models.recipients_for_target(target)]
        if not recipients:
            log.info("No subscribers scoped to %s; skipping %s alert",
                     target["name"], kind)
            return
        if kind == "recovery":
            subject, text, html = compose_recovery(target, state, down_since)
        else:
            subject, text, html = compose_down(target, state)
        send_email(recipients, subject, text, html)
    except Exception:  # noqa: BLE001 - alerting must never take down the monitor
        log.exception("Failed to build or send %s alert for target %s", kind, target["id"])


def send_test_email(recipient: str) -> tuple:
    instance = reporting_instance()
    rows = [
        ("Reporting instance", instance),
        ("SMTP mode", settings.get("smtp_mode")),
        ("SMTP host", settings.get("smtp_host")),
        ("SMTP port", settings.get("smtp_port")),
        ("Security", settings.get("smtp_security")),
        ("From", _from_address()),
        ("Sent at", utcnow_iso()),
    ]
    text = ("ServerPinger test email.\n\n%s\n\n"
            "If you received this, outbound mail works.\n" % _text_from_rows(rows))
    return send_email(
        [recipient],
        "[ServerPinger] Test email from %s" % instance,
        text,
        _wrap_html("ServerPinger test email", "#4f8cff", rows),
        force=True,
    )
