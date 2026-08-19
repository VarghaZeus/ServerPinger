# Changelog

All notable changes to ServerPinger are recorded here. The `VERSION` file is the
single source of truth for the current version; tag releases as `vX.Y.Z` so the
update check can find them.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-19

First release.

### Added

- SQLite schema (`targets`, `target_state`, `check_history`, `subscribers`,
  `settings`) with `schema_version` in settings and an ordered migration list
  applied at startup.
- Full target CRUD with enable/disable and syntax-only host validation
  (IPv4, IPv6, hostname, FQDN — resolution is not required).
- ICMP, TCP and HTTP(S) checks. ICMP shells out to the platform `ping` so it
  works without root or Administrator.
- Background monitor thread started once from the app factory, running checks
  concurrently in a `ThreadPoolExecutor` (default 10 workers).
- Dashboard grouped by `group_name`, with status pills, latency, relative
  last-checked times, 24h uptime, header counts, per-target "Check now", and a
  JSON poll against `/api/status`.
- Email alerting: down alert at the failure threshold, at most one reminder per
  crash-reminder throttle while down, and a recovery email with total downtime.
  Every alert names the reporting instance hostname.
- Email settings page with internal-relay and SMTP AUTH modes, security
  selector, never-rendered password, enable switch, throttle, recovery toggle,
  and a separate test-email card that reports the real SMTP exception.
- Subscribers with optional per-target or per-group scoping.
- Target detail page with recent check history and a server-rendered inline SVG
  latency sparkline.
- Versioning from the `VERSION` file, shown in the footer and exposed at
  `GET /api/version`, plus a release-tag update check at startup and every 6
  hours that fails silently when offline.
- Installers and updaters for Linux/macOS (`install.sh`, `update.sh`, systemd
  unit) and Windows (`install.ps1`, `update.ps1`, NSSM / `sc.exe` /
  Task Scheduler).
- Logging to stdout and to a rotating file in the data directory.
