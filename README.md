# ServerPinger

A general-purpose reachability monitor. It watches any host on any network —
LAN servers, Raspberry Pis, Windows boxes, VMs, public endpoints — and emails you
when one goes down and again when it recovers.

Flask + SQLite + waitress. No ORM, no build step, no npm, no scheduler daemon.
It runs unmodified on Linux, Windows and macOS.

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Quick start (plain terminal, any OS)](#quick-start-plain-terminal-any-os)
- [Install on Linux / macOS](#install-on-linux--macos)
- [Install on Windows](#install-on-windows)
- [Firewall](#firewall)
- [Configuration](#configuration)
- [Email settings](#email-settings)
- [Check types](#check-types)
- [Data, logs and retention](#data-logs-and-retention)
- [Versioning and updates](#versioning-and-updates)
- [Repo layout](#repo-layout)
- [Portability notes](#portability-notes)

---

## What it does

- Checks each target on its own interval via **ICMP**, **TCP** or **HTTP(S)**.
- Declares a target DOWN after N consecutive failures, then emails every
  subscriber scoped to it.
- While it stays down, sends at most one reminder per *crash-reminder throttle*
  hours.
- Emails again on recovery, with the total downtime.
- Every alert names the **reporting instance** (the hostname of the machine
  running ServerPinger), so overlapping instances are distinguishable.
- Dashboard with per-group status, latency, 24h uptime and a "Check now" button.
- Per-target history with a server-rendered SVG latency sparkline.

No auth, no users, no roles. Put it on a trusted network or behind your own
reverse proxy.

## Requirements

- **Python 3.9 or newer.**
- Nothing else. All dependencies are pure Python and pinned in
  `requirements.txt`, so `pip install` works on a 32-bit ARM Raspberry Pi, a
  Windows Server and an x86 Linux box without a C toolchain.
- The system `ping` binary, if you want ICMP checks (present by default on all
  three platforms). No root or Administrator is needed.

## Quick start (plain terminal, any OS)

Useful for testing before you install anything permanent.

**Linux / macOS**

```sh
git clone <your-repo-url> ServerPinger
cd ServerPinger
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py --init-db      # create the database, print its path
.venv/bin/python run.py                # serve on http://0.0.0.0:8282/
```

**Windows (PowerShell)**

```powershell
git clone <your-repo-url> ServerPinger
cd ServerPinger
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --init-db
.\.venv\Scripts\python.exe run.py
```

Then open <http://localhost:8282/>.

Ctrl-C stops it. The monitor thread is a daemon thread, so it dies with the
process.

## Install on Linux / macOS

```sh
sudo ./deploy/install.sh
```

It creates `.venv`, installs the pinned requirements, initialises the database,
and — on Linux with systemd — installs and enables
`/etc/systemd/system/serverpinger.service` with `Restart=always`,
`RestartSec=5`, running as the **unprivileged** user that invoked `sudo`
(never root). Finally it prints the URL.

Override the defaults with environment variables:

```sh
sudo SERVERPINGER_PORT=9000 SERVERPINGER_DATA=/srv/serverpinger ./deploy/install.sh
```

Manage and inspect it:

```sh
sudo systemctl status serverpinger
sudo systemctl restart serverpinger
journalctl -u serverpinger -f
```

On macOS (or a non-systemd Linux) the installer stops after the database step
and prints the exact command to run; wrap that in a launchd plist if you want it
at boot.

Update later:

```sh
./deploy/update.sh          # git pull, reinstall, migrate, restart
```

## Install on Windows

From an **elevated** PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\install.ps1
```

This creates `.venv`, installs requirements, initialises the database in
`%PROGRAMDATA%\ServerPinger`, writes a launcher at
`deploy\serverpinger-service.cmd`, registers ServerPinger to start
automatically, and prints the URL.

Options:

```powershell
.\deploy\install.ps1 -Port 9000 -DataDir "D:\ServerPinger" -OpenFirewall
.\deploy\install.ps1 -Method ScheduledTask
```

### Which registration method?

**1. NSSM (recommended).** If `nssm.exe` is on `PATH`, `-Method Auto` uses it.
NSSM is a proper service wrapper: it handles start/stop correctly, restarts on
crash, and injects the environment variables.

```powershell
# install NSSM first (choco install nssm, or drop nssm.exe somewhere on PATH)
.\deploy\install.ps1 -Method Nssm
nssm restart ServerPinger
nssm status ServerPinger
```

**2. `sc.exe` against the wrapper.** No extra downloads, but Windows expects a
real service binary that talks to the Service Control Manager. A `.cmd` wrapper
does not, so the SCM commonly reports **error 1053 ("did not respond in a timely
fashion")** even though the command itself is fine. Use it only if you cannot
install NSSM and cannot use Task Scheduler:

```powershell
.\deploy\install.ps1 -Method Sc

# equivalent by hand:
sc.exe create ServerPinger binPath= "cmd.exe /c \"C:\ServerPinger\deploy\serverpinger-service.cmd\"" start= auto
sc.exe failure ServerPinger reset= 60 actions= restart/5000/restart/5000/restart/5000
sc.exe start ServerPinger
```

**3. Task Scheduler (the no-install-rights fallback).** Reliable, needs no
third-party binary. The installer registers a task that runs **at startup,
whether the user is logged on or not**, as `SYSTEM`, with no execution time
limit and automatic restart:

```powershell
.\deploy\install.ps1 -Method ScheduledTask

schtasks /query /tn ServerPinger
schtasks /run   /tn ServerPinger
schtasks /end   /tn ServerPinger
```

Update later:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1
```

## Firewall

The UI binds to `0.0.0.0:8282` by default. To reach it from another machine you
must open the port.

**Linux**

```sh
sudo ufw allow 8282/tcp                                   # Debian/Ubuntu
sudo firewall-cmd --add-port=8282/tcp --permanent         # RHEL/Fedora
sudo firewall-cmd --reload
```

**Windows** (elevated PowerShell)

```powershell
New-NetFirewallRule -DisplayName "ServerPinger 8282" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8282
```

**macOS** — the built-in firewall prompts per-application; allow the Python
binary from your venv, or turn the application firewall off for that binary.

## Configuration

Everything is environment variables, with defaults:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SERVERPINGER_PORT` | `8282` | TCP port for the web UI |
| `SERVERPINGER_HOST` | `0.0.0.0` | Bind address (`127.0.0.1` to keep it local) |
| `SERVERPINGER_DATA` | platform default | Directory for the database and log |
| `SERVERPINGER_SECRET_KEY` | generated and stored | Flask session key |

**Data directory resolution**, in priority order:

1. `SERVERPINGER_DATA`
2. `%PROGRAMDATA%\ServerPinger` on Windows, `~/.local/share/serverpinger` on
   Linux/macOS
3. a `data/` directory next to the app

It is created if missing. UNC paths (`\\server\share\ServerPinger`) are
supported; mapped drive letters are not, because services do not see them.

Two further knobs live in the `settings` table rather than the UI, because they
are set-and-forget:

| Key | Default | Meaning |
| --- | --- | --- |
| `history_retention_days` | `7` | Check history older than this is pruned hourly |
| `max_workers` | `10` | Concurrent check workers (restart to apply) |

```sh
sqlite3 ~/.local/share/serverpinger/serverpinger.db \
  "UPDATE settings SET value='14' WHERE key='history_retention_days';"
```

## Email settings

Configure on **Email settings** (`/settings/email`). Two modes:

**Internal relay (no auth, port 25)** — your organisation's mail-protection host,
no credentials. Only works when sending from a trusted internal IP to an
internal mailbox. ServerPinger makes no `login()` call at all in this mode.

Worked example:

```
Server:   cuisinesolutions-com.mail.protection.outlook.com
Port:     25
Security: none / no authentication
From:     ServerPinger <pi-alerts@cuisinesolutions.com>
Note:     only works from a trusted internal IP to an internal mailbox
```

**SMTP AUTH (username & password)** — a normal authenticated server, for example
`smtp.office365.com:587` with STARTTLS.

Notes:

- Changing the mode prefills host/port/security; every field stays editable.
- The stored password is never rendered back into the page. Leave the field
  blank to keep it; type a new value to replace it.
- Ships **unconfigured**: empty host, port 25, security none, sending disabled.
- **Send a test email** is a separate card. It works even while sending is
  disabled, and on failure it shows the actual SMTP exception text. Alert
  recipients are managed on the **Subscribers** page — the test only proves the
  server accepts mail from this host.
- Sending never blocks or kills the monitor loop. Failures are logged and the
  last error is shown at the top of the settings page.

Subscribers can be scoped to **all targets**, **one group**, or **one target**.

## Check types

| Type | What it does | Use when |
| --- | --- | --- |
| `icmp` | Runs the system `ping` once | The host answers ping |
| `tcp` | `socket.create_connection((host, port), timeout)` | ICMP is dropped; pick a port it listens on (22, 445, 3389, 502…) |
| `http` | `urllib.request` GET, compares the status code | You want to know the *service* is alive, not just the box |

Plenty of hosts and firewalls silently drop ICMP — including most Windows
servers with the default firewall and most cloud endpoints. If ping fails but
the machine is clearly alive, switch to TCP or HTTP. The UI says so next to the
check-type selector.

HTTP specifics:

- Ports **443** and **8443** are requested over `https://`; anything else over
  `http://`. Leave the port blank for plain port 80.
- `expect_status` defaults to 200 — set it to 401 or 302 if that is what the
  endpoint legitimately returns.
- `verify_tls` can be turned off for internal self-signed certificates.

Hosts may be IPv4, IPv6, hostnames or FQDNs, private or public. ServerPinger
does not check what subnet you are on, does not restrict to RFC1918, and never
scans or auto-discovers anything. A host is validated for *syntax* only — it
does not have to resolve when you add it, because it may legitimately be down.

## Data, logs and retention

- Database: `<data dir>/serverpinger.db` (SQLite, WAL mode).
- Log: stdout **and** `<data dir>/serverpinger.log`, rotating at 1 MB with 5
  backups. The file handler exists because Windows services swallow stdout;
  on Linux use `journalctl -u serverpinger -f`.
- `check_history` is pruned hourly to `history_retention_days` (default 7).
- All timestamps are stored as UTC ISO-8601 and converted to **your browser's**
  local time by a small vanilla JS helper, so the monitoring host's timezone
  does not matter.

## Versioning and updates

- `VERSION` holds a semver string and is the single source of truth. It is read
  at startup, shown in the footer, and exposed at `GET /api/version`.
- At startup and every 6 hours ServerPinger asks the configured Git remote for
  its newest release tag (`git ls-remote --tags`) and caches the answer with a
  timestamp.
- The footer shows one of:
  `up to date (checked N min ago)` · `update available: vX.Y.Z` ·
  `update check failed` · `update check off`.
- The repo URL and whether checking runs at all are configurable on the Email
  settings page. Leave the URL blank to use the `origin` of this checkout.
- **It fails silently when offline.** An air-gapped host keeps monitoring and
  alerting normally.
- Updating is manual: run `deploy/update.sh` or `deploy\update.ps1`. There is no
  self-update button in the web UI on purpose.

Migrations are a `schema_version` value in `settings` plus an ordered list of
functions in `app/schema.py`, applied at startup. To add one, append a
`(name, function)` pair to `MIGRATIONS` — never renumber existing entries.

## Repo layout

```
app/                    application package
  __init__.py           app factory, logging
  paths.py              data-directory resolution, VERSION
  db.py                 per-thread sqlite3 connections
  schema.py             schema + ordered migrations
  settings.py           key/value settings with defaults
  models.py             queries shared by web and monitor threads
  checks.py             ICMP / TCP / HTTP probes
  monitor.py            background thread + ThreadPoolExecutor
  mailer.py             SMTP delivery and alert composition
  updates.py            release-tag update check
  views.py              routes, forms, JSON API, SVG sparkline
  util.py               UTC timestamps, host validation
run.py                  entrypoint (waitress); --init-db
templates/              Jinja2 templates
static/css/app.css      the only stylesheet
static/js/app.js        timestamp localisation + dashboard poll
deploy/
  serverpinger.service  systemd unit
  install.sh            Linux/macOS installer
  install.ps1           Windows installer
  update.sh
  update.ps1
requirements.txt
VERSION
CHANGELOG.md
```

## Portability notes

- `from __future__ import annotations` everywhere, so Python 3.9 works; nothing
  used here breaks on 3.12+.
- Production WSGI server is **waitress** — pure Python, identical on Windows and
  Linux. Not gunicorn (Linux-only), and never the Flask dev server.
- Every path goes through `pathlib`. No hardcoded `/home/pi`, no drive letters.
- Every `subprocess` call passes a list of args with `shell=False` and an
  explicit `timeout`, so a wedged `ping` cannot hang a worker.
- ICMP branches on `platform.system()`: `ping -c 1 -W <seconds>` on Linux,
  `ping -c 1 -W <milliseconds>` on macOS (its `-W` is in ms), and
  `ping -n 1 -w <milliseconds>` on Windows. Latency is parsed from stdout with a
  regex covering both formats; a non-zero exit code is always a failure, and on
  Windows a reply without `TTL=` is treated as a failure too, because Windows
  `ping` exits 0 for "Destination host unreachable".
- The one dependency with optional C code is MarkupSafe (a Flask dependency); it
  falls back to a pure-Python implementation when no compiler is present.
