"""ServerPinger application factory."""
from __future__ import annotations

import logging
import logging.handlers
import os
import secrets
import sys

from flask import Flask

from .paths import APP_ROOT, data_dir, log_path, version

_logging_configured = False


def configure_logging(level=logging.INFO) -> None:
    """Log to stdout (journalctl) and to a rotating file (Windows services eat stdout)."""
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    try:
        rotating = logging.handlers.RotatingFileHandler(
            str(log_path()), maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        rotating.setFormatter(formatter)
        root.addHandler(rotating)
    except OSError as exc:  # a read-only data dir must not stop the app
        root.warning("Could not open log file %s: %s", log_path(), exc)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _secret_key() -> str:
    from . import settings

    env_key = os.environ.get("SERVERPINGER_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    stored = settings.get("secret_key")
    if stored:
        return stored
    generated = secrets.token_hex(32)
    settings.set_value("secret_key", generated)
    return generated


def create_app(start_monitor: bool = True) -> Flask:
    configure_logging()
    log = logging.getLogger(__name__)

    from . import models, monitor, schema, settings, updates, views

    app = Flask(
        __name__,
        template_folder=str(APP_ROOT / "templates"),
        static_folder=str(APP_ROOT / "static"),
    )

    schema.migrate()
    settings.ensure_defaults()

    app.config["SERVERPINGER_VERSION"] = version()
    app.secret_key = _secret_key()
    app.jinja_env.globals["app_version"] = version()

    app.register_blueprint(views.bp)

    @app.context_processor
    def inject_footer():
        try:
            target_count = models.count_targets()
        except Exception:  # noqa: BLE001 - the footer must never break a page
            target_count = 0
        return {
            "footer_version": version(),
            "footer_update": updates.status(),
            "footer_target_count": target_count,
            "monitor_running": monitor.is_running(),
        }

    log.info("ServerPinger v%s starting (data dir: %s)", version(), data_dir())

    if start_monitor:
        monitor.start()

    return app
