"""Small shared helpers: UTC timestamps, host validation, duration formatting."""
from __future__ import annotations

import ipaddress
import re
import socket
from datetime import datetime, timedelta, timezone

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().strftime(ISO_FORMAT)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO_FORMAT)


def parse_iso(value):
    """Parse a stored UTC timestamp back to an aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, ISO_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def since_iso(hours: float) -> str:
    return iso(utcnow() - timedelta(hours=hours))


def is_valid_host(host: str) -> bool:
    """True for any IPv4/IPv6 literal or syntactically valid hostname/FQDN.

    Deliberately does not resolve: a target may legitimately be down when added.
    """
    host = (host or "").strip()
    if not host:
        return False
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if "%" in host:  # IPv6 zone id, e.g. fe80::1%eth0
        host = host.split("%", 1)[0]
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False
    return bool(_HOSTNAME_RE.match(host))


def is_ipv6(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host.strip("[]")), ipaddress.IPv6Address)
    except ValueError:
        return False


def reporting_instance() -> str:
    """Hostname of the machine running this ServerPinger instance."""
    try:
        return socket.gethostname() or "unknown-host"
    except OSError:
        return "unknown-host"


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append("%dd" % days)
    if hours:
        parts.append("%dh" % hours)
    if minutes:
        parts.append("%dm" % minutes)
    if not parts or (not days and not hours):
        parts.append("%ds" % secs)
    return " ".join(parts)
