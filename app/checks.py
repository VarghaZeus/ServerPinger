"""Reachability probes: ICMP (via the system ping binary), TCP and HTTP.

ICMP shells out to the platform ping so it works without root/Administrator.
Every subprocess call passes a list of args with shell=False and an explicit
timeout so a wedged ping can never hang a worker.
"""
from __future__ import annotations

import logging
import platform
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections import namedtuple

from .util import is_ipv6

log = logging.getLogger(__name__)

CheckResult = namedtuple("CheckResult", "ok latency_ms error")

CHECK_TYPES = ("icmp", "tcp", "http")

# HTTP checks speak TLS on these ports; anything else is plain http://.
HTTPS_PORTS = (443, 8443)

# Matches Linux/macOS "time=0.123 ms" and Windows "time=1ms" / "time<1ms",
# including localised Windows builds where the label word differs.
_LATENCY_RE = re.compile(
    r"(?:time|tiempo|zeit|temps|tempo)\s*[=<]\s*([0-9]+(?:[.,][0-9]+)?)\s*ms",
    re.IGNORECASE,
)
_TTL_RE = re.compile(r"\bttl\s*=\s*\d+", re.IGNORECASE)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PING_FAILURE_WORDS = (
    "unreachable", "unknown host", "not find host", "timed out", "failure",
    "expired", "name or service", "no route", "100%",
)


def _ping_command(host, timeout_seconds):
    system = platform.system()
    if system == "Windows":
        # -n <count>, -w <timeout in MILLISECONDS>
        millis = max(1, int(round(timeout_seconds * 1000)))
        return ["ping", "-n", "1", "-w", str(millis), host]
    if system == "Darwin":
        # macOS ping also takes -W in milliseconds.
        millis = max(1, int(round(timeout_seconds * 1000)))
        return ["ping", "-c", "1", "-W", str(millis), host]
    # Linux and other POSIX: -W is whole seconds.
    seconds = max(1, int(round(timeout_seconds)))
    return ["ping", "-c", "1", "-W", str(seconds), host]


def check_icmp(host, timeout_seconds):
    host = host.strip().strip("[]")
    command = _ping_command(host, timeout_seconds)
    backstop = max(2.0, float(timeout_seconds) + 5.0)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=backstop,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(False, None, "ping timed out after %.0fs" % backstop)
    except FileNotFoundError:
        return CheckResult(False, None, "ping binary not found on this system")
    except OSError as exc:
        return CheckResult(False, None, "ping failed to start: %s" % exc)

    elapsed_ms = (time.monotonic() - started) * 1000.0
    output = completed.stdout.decode("utf-8", errors="replace") if completed.stdout else ""

    if completed.returncode != 0:
        return CheckResult(False, None, _ping_error(output, completed.returncode))

    # Windows ping exits 0 for "Destination host unreachable" and "TTL expired",
    # so the exit code alone is not enough there.
    if platform.system() == "Windows" and not _TTL_RE.search(output):
        return CheckResult(False, None, _ping_error(output, completed.returncode))

    match = _LATENCY_RE.search(output)
    if match:
        latency = float(match.group(1).replace(",", "."))
    else:
        latency = round(elapsed_ms, 2)
    return CheckResult(True, latency, None)


def _ping_error(output, returncode):
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        lowered = line.lower()
        for word in _PING_FAILURE_WORDS:
            if word in lowered:
                return line[:300]
    text = " ".join(output.split())
    return text[:300] if text else "ping exited with code %d" % returncode


def check_tcp(host, port, timeout_seconds):
    host = host.strip().strip("[]")
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_seconds):
            latency = (time.monotonic() - started) * 1000.0
        return CheckResult(True, round(latency, 2), None)
    except socket.timeout:
        return CheckResult(
            False, None,
            "TCP connect to port %s timed out after %ss" % (port, timeout_seconds),
        )
    except OSError as exc:
        return CheckResult(False, None, "TCP connect to port %s failed: %s" % (port, exc))


def build_http_url(host, port, path):
    host = (host or "").strip().strip("[]")
    try:
        port_number = int(port) if port else None
    except (TypeError, ValueError):
        port_number = None
    scheme = "https" if port_number in HTTPS_PORTS else "http"
    authority = "[%s]" % host if is_ipv6(host) else host
    if port_number and port_number not in (80, 443):
        authority = "%s:%d" % (authority, port_number)
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return "%s://%s%s" % (scheme, authority, path)


def check_http(host, port, path, expect_status, verify_tls, timeout_seconds):
    url = build_http_url(host, port, path)
    try:
        expect_status = int(expect_status or 200)
    except (TypeError, ValueError):
        expect_status = 200
    context = None
    if url.startswith("https://"):
        context = ssl.create_default_context()
        if not verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        url, method="GET", headers={"User-Agent": "ServerPinger", "Accept": "*/*"}
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
            status = response.getcode()
            response.read(2048)  # drain a little so the connection closes cleanly
    except urllib.error.HTTPError as exc:
        # A non-2xx response is still a reachable server; it may even be expected.
        status = exc.code
        try:
            exc.read(2048)
        except Exception:  # noqa: BLE001 - draining is best effort
            pass
    except urllib.error.URLError as exc:
        return CheckResult(False, None, "%s: %s" % (url, exc.reason))
    except socket.timeout:
        return CheckResult(False, None, "%s timed out after %ss" % (url, timeout_seconds))
    except (OSError, ValueError) as exc:
        return CheckResult(False, None, "%s: %s" % (url, exc))

    latency = round((time.monotonic() - started) * 1000.0, 2)
    if status != expect_status:
        return CheckResult(
            False, latency,
            "%s returned HTTP %s, expected %s" % (url, status, expect_status),
        )
    return CheckResult(True, latency, None)


def run_check(target):
    """Dispatch on check_type. `target` is a sqlite3.Row (or mapping) of a target."""
    check_type = (target["check_type"] or "icmp").lower()
    try:
        timeout = float(target["timeout_seconds"] or 5)
    except (TypeError, ValueError):
        timeout = 5.0
    host = target["host"]
    try:
        if check_type == "tcp":
            if not target["port"]:
                return CheckResult(False, None, "TCP check has no port configured")
            return check_tcp(host, target["port"], timeout)
        if check_type == "http":
            return check_http(
                host, target["port"], target["path"], target["expect_status"],
                bool(target["verify_tls"]), timeout,
            )
        return check_icmp(host, timeout)
    except Exception as exc:  # noqa: BLE001 - a probe must never kill its worker
        log.exception("Unexpected error checking %s", host)
        return CheckResult(False, None, "internal error: %s" % exc)
