# Changelog

## 0.2.0 — 2026-10-20

- Restore the complete 0.1.0 project as the initial GitHub source.
- Add a read-only `doctor` command, with optional public-data checks using `--live`.
- Report partial Telegram configuration without exposing secret values; never send messages in doctor.
- Reject incomplete market snapshots at the final fetch timestamp before live processing and heartbeat updates.
- Replace heartbeat files atomically and include snapshot time and data source.
- Install timezone data on Windows for Tehran risk-day boundaries.
- Add eight regression tests and a Persian guide for reviewing progress from a phone.
- Preserve the strategy rules and keep paper trading disabled by default.
