"""All HTTP routes: dashboard, targets, subscribers, email settings, JSON API."""
from __future__ import annotations

import logging

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, url_for)
from markupsafe import Markup

from . import mailer, models, monitor, settings, updates
from .checks import CHECK_TYPES, build_http_url
from .util import is_valid_host, parse_iso

log = logging.getLogger(__name__)

bp = Blueprint("main", __name__)

STATUS_ORDER = {"down": 0, "unknown": 1, "up": 2}


# ------------------------------------------------------------------ dashboard

def _target_payload(target) -> dict:
    return {
        "id": target["id"],
        "name": target["name"],
        "host": target["host"],
        "group_name": target["group_name"] or "",
        "check_type": target["check_type"],
        "port": target["port"],
        "enabled": bool(target["enabled"]),
        "status": (target["status"] or "unknown") if target["enabled"] else "unknown",
        "consecutive_failures": target["consecutive_failures"] or 0,
        "last_checked_at": target["last_checked_at"],
        "last_latency_ms": target["last_latency_ms"],
        "last_error": target["last_error"],
        "uptime_24h": models.uptime_percent(target["id"], 24),
    }


def _grouped(payloads):
    groups = {}
    for item in payloads:
        groups.setdefault(item["group_name"] or "Ungrouped", []).append(item)
    ordered = []
    for name in sorted(groups, key=lambda g: (g == "Ungrouped", g.lower())):
        rows = sorted(groups[name],
                      key=lambda r: (STATUS_ORDER.get(r["status"], 3), r["name"].lower()))
        ordered.append({"name": name, "targets": rows})
    return ordered


@bp.route("/")
def dashboard():
    payloads = [_target_payload(t) for t in models.list_targets()]
    return render_template("dashboard.html",
                           groups=_grouped(payloads),
                           counts=models.status_counts(),
                           total=len(payloads))


@bp.route("/api/status")
def api_status():
    payloads = [_target_payload(t) for t in models.list_targets()]
    return jsonify({
        "counts": models.status_counts(),
        "total": len(payloads),
        "groups": _grouped(payloads),
        "monitor_running": monitor.is_running(),
    })


@bp.route("/api/version")
def api_version():
    return jsonify(updates.status())


@bp.route("/api/targets/<int:target_id>/check", methods=["POST"])
def api_check_now(target_id: int):
    target = models.get_target(target_id)
    if target is None:
        abort(404)
    result = monitor.check_now(target_id)
    if result is None:
        return jsonify({"ok": None, "message": "a check is already running"}), 202
    return jsonify({
        "ok": bool(result.ok),
        "latency_ms": result.latency_ms,
        "error": result.error,
        "target": _target_payload(models.get_target(target_id)),
    })


# --------------------------------------------------------------------- targets

def _read_target_form(form):
    errors = []
    values = {}

    values["name"] = (form.get("name") or "").strip()
    if not values["name"]:
        errors.append("Name is required.")

    host = (form.get("host") or "").strip()
    values["host"] = host
    if not host:
        errors.append("Host is required.")
    elif not is_valid_host(host):
        errors.append("Host must be an IPv4 or IPv6 address, a hostname, or an FQDN.")

    check_type = (form.get("check_type") or "icmp").strip().lower()
    if check_type not in CHECK_TYPES:
        errors.append("Check type must be one of: %s." % ", ".join(CHECK_TYPES))
        check_type = "icmp"
    values["check_type"] = check_type

    port_text = (form.get("port") or "").strip()
    port = None
    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            errors.append("Port must be a number.")
        else:
            if not 1 <= port <= 65535:
                errors.append("Port must be between 1 and 65535.")
    values["port"] = port
    if check_type == "tcp" and port is None:
        errors.append("TCP checks need a port.")

    path = (form.get("path") or "/").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    values["path"] = path

    values["expect_status"] = _positive_int(form.get("expect_status"), 200, 100, 599,
                                            "Expected status", errors)
    values["interval_seconds"] = _positive_int(form.get("interval_seconds"), 60, 5, 86400,
                                               "Interval", errors)
    values["timeout_seconds"] = _positive_int(form.get("timeout_seconds"), 5, 1, 300,
                                              "Timeout", errors)
    values["fail_threshold"] = _positive_int(form.get("fail_threshold"), 3, 1, 100,
                                             "Failure threshold", errors)

    values["group_name"] = (form.get("group_name") or "").strip() or None
    values["notes"] = (form.get("notes") or "").strip() or None
    values["enabled"] = 1 if form.get("enabled") else 0
    values["verify_tls"] = 1 if form.get("verify_tls") else 0
    return values, errors


def _positive_int(raw, default, low, high, label, errors):
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        errors.append("%s must be a number." % label)
        return default
    if not low <= value <= high:
        errors.append("%s must be between %d and %d." % (label, low, high))
        return default
    return value


@bp.route("/targets")
def targets():
    return render_template("targets.html", targets=models.list_targets())


@bp.route("/targets/new", methods=["GET", "POST"])
def target_new():
    if request.method == "POST":
        values, errors = _read_target_form(request.form)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("target_form.html", target=values, is_new=True,
                                   groups=models.known_groups())
        target_id = models.create_target(values)
        flash("Added target %s." % values["name"], "success")
        return redirect(url_for("main.target_detail", target_id=target_id))

    defaults = {
        "name": "", "host": "", "enabled": 1, "group_name": "", "check_type": "icmp",
        "port": "", "path": "/", "expect_status": 200, "verify_tls": 1,
        "interval_seconds": 60, "timeout_seconds": 5, "fail_threshold": 3, "notes": "",
    }
    return render_template("target_form.html", target=defaults, is_new=True,
                           groups=models.known_groups())


@bp.route("/targets/<int:target_id>/edit", methods=["GET", "POST"])
def target_edit(target_id: int):
    target = models.get_target(target_id)
    if target is None:
        abort(404)
    if request.method == "POST":
        values, errors = _read_target_form(request.form)
        if errors:
            for error in errors:
                flash(error, "error")
            values["id"] = target_id
            return render_template("target_form.html", target=values, is_new=False,
                                   groups=models.known_groups())
        models.update_target(target_id, values)
        flash("Saved %s." % values["name"], "success")
        return redirect(url_for("main.target_detail", target_id=target_id))
    return render_template("target_form.html", target=target, is_new=False,
                           groups=models.known_groups())


@bp.route("/targets/<int:target_id>/delete", methods=["POST"])
def target_delete(target_id: int):
    target = models.get_target(target_id)
    if target is None:
        abort(404)
    models.delete_target(target_id)
    flash("Deleted %s." % target["name"], "success")
    return redirect(url_for("main.targets"))


@bp.route("/targets/<int:target_id>/toggle", methods=["POST"])
def target_toggle(target_id: int):
    target = models.get_target(target_id)
    if target is None:
        abort(404)
    models.set_target_enabled(target_id, not target["enabled"])
    flash("%s %s." % (target["name"], "disabled" if target["enabled"] else "enabled"),
          "success")
    return redirect(request.referrer or url_for("main.targets"))


@bp.route("/targets/<int:target_id>")
def target_detail(target_id: int):
    target = models.get_target(target_id)
    if target is None:
        abort(404)
    history = models.recent_history(target_id, 200)
    url = None
    if target["check_type"] == "http":
        url = build_http_url(target["host"], target["port"], target["path"])
    return render_template(
        "target_detail.html",
        target=target,
        history=history,
        sparkline=Markup(render_sparkline(history)),
        uptime_24h=models.uptime_percent(target_id, 24),
        uptime_7d=models.uptime_percent(target_id, 24 * 7),
        http_url=url,
    )


def render_sparkline(history, width: int = 720, height: int = 90) -> str:
    """Inline SVG latency sparkline, drawn server-side. No chart library."""
    rows = list(reversed(list(history)))  # oldest first
    if not rows:
        return ('<svg viewBox="0 0 %d %d" class="sparkline" role="img" '
                'aria-label="No history yet"><text x="12" y="%d" fill="#8b93a7" '
                'font-size="13">No history yet.</text></svg>'
                % (width, height, height // 2))

    pad = 8
    inner_h = height - pad * 2
    latencies = [row["latency_ms"] for row in rows if row["ok"] and row["latency_ms"] is not None]
    top = max(latencies) if latencies else 1.0
    if top <= 0:
        top = 1.0
    step = (width - pad * 2) / max(1, len(rows) - 1)

    points = []
    failures = []
    for index, row in enumerate(rows):
        x = pad + index * step
        if row["ok"] and row["latency_ms"] is not None:
            y = pad + inner_h - (float(row["latency_ms"]) / top) * inner_h
            points.append("%.1f,%.1f" % (x, y))
        elif not row["ok"]:
            failures.append(x)

    parts = ['<svg viewBox="0 0 %d %d" class="sparkline" role="img" '
             'aria-label="Latency over the last %d checks" preserveAspectRatio="none">'
             % (width, height, len(rows))]
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#232937" '
                 'stroke-width="1"/>' % (pad, pad + inner_h, width - pad, pad + inner_h))
    for x in failures:
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#ff6b6b" '
                     'stroke-width="2" opacity="0.65"/>' % (x, pad, x, pad + inner_h))
    if len(points) >= 2:
        parts.append('<polyline fill="none" stroke="#4f8cff" stroke-width="1.8" '
                     'stroke-linejoin="round" points="%s"/>' % " ".join(points))
    elif len(points) == 1:
        x, y = points[0].split(",")
        parts.append('<circle cx="%s" cy="%s" r="2.5" fill="#4f8cff"/>' % (x, y))
    parts.append('<text x="%d" y="%d" fill="#8b93a7" font-size="11">peak %.0f ms</text>'
                 % (pad, pad + 10, top))
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------- subscribers

@bp.route("/subscribers")
def subscribers():
    return render_template("subscribers.html",
                           subscribers=models.list_subscribers(),
                           targets=models.list_targets(),
                           groups=models.known_groups())


def _read_subscriber_form(form):
    errors = []
    email = (form.get("email") or "").strip()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        errors.append("A valid email address is required.")
    scope = (form.get("scope") or "all").strip()
    target_id = None
    group_name = None
    if scope == "target":
        try:
            target_id = int(form.get("target_id") or 0) or None
        except ValueError:
            target_id = None
        if target_id is None:
            errors.append("Pick a target for a per-target subscriber.")
    elif scope == "group":
        group_name = (form.get("group_name") or "").strip() or None
        if group_name is None:
            errors.append("Pick a group for a per-group subscriber.")
    values = {
        "email": email,
        "name": (form.get("name") or "").strip() or None,
        "enabled": 1 if form.get("enabled") else 0,
        "target_id": target_id,
        "group_name": group_name,
    }
    return values, errors


@bp.route("/subscribers/new", methods=["POST"])
def subscriber_new():
    values, errors = _read_subscriber_form(request.form)
    if errors:
        for error in errors:
            flash(error, "error")
    else:
        models.create_subscriber(values)
        flash("Added subscriber %s." % values["email"], "success")
    return redirect(url_for("main.subscribers"))


@bp.route("/subscribers/<int:subscriber_id>/edit", methods=["POST"])
def subscriber_edit(subscriber_id: int):
    if models.get_subscriber(subscriber_id) is None:
        abort(404)
    values, errors = _read_subscriber_form(request.form)
    if errors:
        for error in errors:
            flash(error, "error")
    else:
        models.update_subscriber(subscriber_id, values)
        flash("Saved %s." % values["email"], "success")
    return redirect(url_for("main.subscribers"))


@bp.route("/subscribers/<int:subscriber_id>/delete", methods=["POST"])
def subscriber_delete(subscriber_id: int):
    subscriber = models.get_subscriber(subscriber_id)
    if subscriber is None:
        abort(404)
    models.delete_subscriber(subscriber_id)
    flash("Removed %s." % subscriber["email"], "success")
    return redirect(url_for("main.subscribers"))


@bp.route("/subscribers/<int:subscriber_id>/toggle", methods=["POST"])
def subscriber_toggle(subscriber_id: int):
    subscriber = models.get_subscriber(subscriber_id)
    if subscriber is None:
        abort(404)
    models.set_subscriber_enabled(subscriber_id, not subscriber["enabled"])
    return redirect(url_for("main.subscribers"))


# -------------------------------------------------------------- email settings

@bp.route("/settings/email", methods=["GET", "POST"])
def settings_email():
    if request.method == "POST":
        form = request.form
        mode = (form.get("smtp_mode") or "internal_relay").strip()
        if mode not in settings.SMTP_MODES:
            mode = "internal_relay"
        security = (form.get("smtp_security") or "none").strip()
        if security not in settings.SMTP_SECURITY:
            security = "none"

        values = {
            "smtp_mode": mode,
            "smtp_host": (form.get("smtp_host") or "").strip(),
            "smtp_port": _positive_int(form.get("smtp_port"), 25, 1, 65535,
                                       "SMTP port", []),
            "smtp_security": security,
            "smtp_username": (form.get("smtp_username") or "").strip(),
            "smtp_from": (form.get("smtp_from") or "").strip(),
            "email_enabled": 1 if form.get("email_enabled") == "1" else 0,
            "crash_reminder_hours": _positive_int(form.get("crash_reminder_hours"),
                                                  24, 0, 8760, "Reminder throttle", []),
            "send_recovery_emails": 1 if form.get("send_recovery_emails") == "1" else 0,
            "update_check_enabled": 1 if form.get("update_check_enabled") == "1" else 0,
            "update_repo_url": (form.get("update_repo_url") or "").strip(),
        }
        # The stored password is never rendered back; only overwrite it when a new
        # value is actually submitted.
        new_password = form.get("smtp_password") or ""
        if new_password.strip():
            values["smtp_password"] = new_password
        if mode == "internal_relay":
            values["smtp_username"] = ""
            values["smtp_password"] = ""
        settings.set_many(values)
        flash("Email settings saved.", "success")
        return redirect(url_for("main.settings_email"))

    values = settings.all_settings()
    return render_template("settings_email.html", s=values,
                           has_password=bool(values.get("smtp_password")))


@bp.route("/settings/email/test", methods=["POST"])
def settings_email_test():
    recipient = (request.form.get("test_recipient") or "").strip()
    if not recipient or "@" not in recipient:
        flash("Enter a recipient address for the test.", "error")
        return redirect(url_for("main.settings_email"))
    ok, error = mailer.send_test_email(recipient)
    if ok:
        flash("Test email sent to %s." % recipient, "success")
    else:
        flash("Test email failed: %s" % error, "error")
    return redirect(url_for("main.settings_email"))


# ---------------------------------------------------------------------- errors

@bp.app_errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404,
                           message="That page does not exist."), 404


@bp.app_errorhandler(500)
def server_error(error):
    log.exception("Unhandled error: %s", error)
    return render_template("error.html", code=500,
                           message="Something went wrong. Check the log."), 500


@bp.app_template_filter("localtime")
def localtime_filter(value):
    """Render a UTC timestamp as a span the browser JS converts to local time."""
    if not value:
        return Markup('<span class="ts muted">never</span>')
    if parse_iso(value) is None:
        return Markup('<span class="ts muted">%s</span>' % value)
    return Markup('<span class="ts" data-utc="%s">%s</span>' % (value, value))
