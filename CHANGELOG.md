# Changelog

## 0.4.0 — 2026-10-20

- Preserve classic rules and add an optional, versioned four-filter research profile inspired by the reviewed Gemini draft.
- Add timezone-aware entry windows; monitoring and exits continue outside entry hours.
- Add optional staged exits, conservative same-bar breakeven handling, an exact fill/cost ledger and planned-risk metrics.
- Add official checksum-verified USD-M archive downloads and bounded, reproducible backtests with initial-capital and marked-equity drawdowns. Report open positions at the end.
- Add an isolated live chart display cache, freshness checks and a quote-only WebSocket command. Unfinished bars cannot enter recorded decisions or replay.
- Cache unchanged validated frames using all input values and UTC boundaries. Keep default configuration fingerprints backward-compatible.
- No exchange order execution or account connection is introduced.

## 0.3.0 — 2026-10-20

- Add a Persian evidence dashboard with a numeric candle chart, timeframe selection, alert status, decision replay, risk/health views and journal-grounded answers.
- Persist immutable compressed decision inputs, actual observation times and historical risk snapshots; preserve legacy analyses without fabricating missing inputs.
- Add loopback-only HTTP access and read-only journal snapshots. Queue pause/resume commands for the scanner to apply and acknowledge; enforce local session, host and origin checks.
- Add portable evidence export and a synthetic, paper-disabled demonstration generator. The hosted viewer cannot control the scanner.
- Keep trading rules and strategy ID unchanged. No KX integration or real execution is introduced.
- Add ten Python and ten JavaScript regression tests, including observation-time and trade-knowledge boundaries.

## 0.2.0 — 2026-10-20

- Restore the complete 0.1.0 project as the initial GitHub source.
- Add a read-only `doctor` command, with optional public-data checks using `--live`.
- Report partial Telegram configuration without exposing secret values; never send messages in doctor.
- Reject incomplete market snapshots at the final fetch timestamp before live processing and heartbeat updates.
- Replace heartbeat files atomically and include snapshot time and data source.
- Install timezone data on Windows for Tehran risk-day boundaries.
- Add eight regression tests and a Persian guide for reviewing progress from a phone.
- Preserve the strategy rules and keep paper trading disabled by default.
