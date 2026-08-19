# Changelog

All notable changes to ServerPinger are recorded here. The `VERSION` file is the
single source of truth for the current version; tag releases as `vX.Y.Z` so the
update check can find them.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-08-19

### Added

- Per-target action buttons on the Dashboard and Targets pages: pause/resume,
  check now, details, edit, duplicate and delete.
- **Duplicate a target** (`POST /targets/<id>/clone`). The copy is named
  "<name> (copy)", starts paused so it never pings the wrong host, and opens
  straight into the edit form so only the host needs changing.
- Forced update check: the footer status is now a button
  (`POST /api/update-check`), and the reason for a failure is stored and shown
  as its tooltip.
- Pause, resume and delete return to the page they were triggered from.

### Changed

- Default port is now **8282** (was 8080).

### Fixed

- Release tags were matched case-sensitively, so a tag pushed as `V1.0.0`
  rather than `v1.0.0` was ignored and no update was ever reported.
- The update check could hang until its 20 s timeout when git wanted
  credentials or an SSH host-key confirmation. It now runs with
  `GIT_TERMINAL_PROMPT=0` and a `BatchMode=yes` SSH command, so it fails in
  under a second instead, and reports a readable reason.
- A failed update check now retries after 15 minutes instead of waiting the
  full 6 hours, so a check that ran before the network (or the remote) was
  ready recovers on its own.

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
