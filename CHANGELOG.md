# Changelog

All notable changes to OrderFerry are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Add a bounded, snapshot-stable `__rates_page__` history export contract and protocol
  capability discovery for large broker replay datasets.
- Persist the required terminal `maxbars` threshold in the scheduled-task command and expose
  actual/required history readiness through `__status__`.

### Fixed

- Accept multiple firewall address scopes in the scheduled-task installer.

## [1.0.0] - 2026-07-15

### Added

- Thread-safe MT5 runtime with degraded startup and watchdog reconnection.
- Newline-delimited JSON protocol with explicit method allowlisting.
- Market data, tick subscriptions, high-level trading, and canonical errors.
- Interactive Windows Scheduled Task installer with scoped firewall support.
- Reproducible `uv` lockfile, Windows CI across Python 3.10–3.14, and wheel/sdist builds.

### Security

- Loopback-only default bind.
- No automatic edits to MetaTrader terminal configuration.
- Explicit warnings and firewall scoping for remote binds.

[1.0.0]: https://github.com/ElyzeSolutions/OrderFerry/releases/tag/v1.0.0
