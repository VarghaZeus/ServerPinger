"""ServerPinger entrypoint.

Production server is waitress: pure Python, identical on Windows and Linux.

    python run.py              # serve
    python run.py --init-db    # create/migrate the database and exit
"""
from __future__ import annotations

import logging
import os
import sys

from app import configure_logging, create_app
from app.paths import data_dir, db_path, version


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def init_db() -> int:
    configure_logging()
    create_app(start_monitor=False)
    print("ServerPinger v%s" % version())
    print("Data directory: %s" % data_dir())
    print("Database:       %s" % db_path())
    return 0


def main() -> int:
    if "--init-db" in sys.argv:
        return init_db()

    app = create_app()
    host = os.environ.get("SERVERPINGER_HOST", "").strip() or "0.0.0.0"
    port = _env_int("SERVERPINGER_PORT", 8282)

    from waitress import serve

    display_host = "localhost" if host in ("0.0.0.0", "::") else host
    logging.getLogger(__name__).info(
        "Serving ServerPinger v%s on http://%s:%d/", version(), display_host, port
    )
    serve(app, host=host, port=port, threads=8, ident="ServerPinger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
