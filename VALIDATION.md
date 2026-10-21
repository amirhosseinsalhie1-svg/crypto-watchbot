# Validation status — 0.2.0 — 2026-10-20

- Python unit/integration tests: 41 passed (local environment).
- Python wheel packaging: successfully built locally with no dependency downloads.
- Offline demo: generated four three-timeframe PNG charts with synthetic LONG and SHORT confirmation fixtures, Persian captions and JSON evidence.
- Transport tests: mocked Binance OHLC responses, cache behavior, clock-skew rejection, Telegram multipart photo payload and secret-redacted HTTP errors.
- Paper tests: next-bar entry, adverse slippage and costs, stop-first ambiguous candle, gap loss, entry-gap cancellation, one-position limit, news gating, restart idempotency, risk stops, Tehran day boundary, transaction rollback and timeout.
- No look-ahead checks: unfinished candle exclusion, displaced Ichimoku cloud, future reversal exclusion in walk-forward processing.
- Diagnostics regression checks: offline preflight never uses the network or creates a journal; partial credentials fail with redacted output; public-data probe never calls Telegram; failed/incomplete/boundary-crossed snapshots are rejected before analysis and heartbeat updates; CLI returns a failure exit code when appropriate.
- CLI walk-forward smoke test: a deliberately constructed synthetic dataset went through pending entry, fill, exit and CSV export. It is not evidence about real prices or returns.
- Live public data probe from the original environment returned **HTTP 451**. The 0.2.0 `doctor --live` probe in the current environment also failed, returning `market_verified: false`; a live market stream remains **unverified**. No proxy or alternate unofficial endpoint was used.
- Telegram delivery to a real user: **not tested**, no token/destination configured during development.
- Docker configuration supplied; Docker execution and Windows timezone behavior have not been verified locally. GitHub Actions execution status is available separately in the repository's Actions tab.
- No measured strategy win rate, backtested profit, live trading result or safety guarantee. Synthetic fixtures verify behavior, not financial performance.

Run `python -m unittest discover -s tests -v` after installation. Deployment requires a host that can access the selected public data provider legitimately, a private Telegram bot token, an explicit destination and durable storage.

## Python 3.15 compatibility (2026-10-21)

Local Linux validation used CPython 3.15.0rc3 (the available uv runtime),
NumPy 2.5.4, pandas 3.0.6, matplotlib 3.11.2, and websockets 15.0.1.
All 41 existing unit/integration tests passed. Offline doctor returned ok=true,
and synthetic demo completed for all four configured symbols.
Windows and final CPython 3.15.0 were not run locally; CI now includes
Python 3.15 on Windows and Linux. Live market connectivity was not tested.

## MEXC adapter

44 local tests passed on Python 3.12, including mocked MEXC seconds/milliseconds conversion, open-candle exclusion, three-timeframe cache, clock skew rejection, and enabled perpetual filtering. Offline MEXC doctor passed. Live full-snapshot access could not be verified from this environment (non-JSON network responses). User independently verified direct MEXC ping and 2000 BTC Min5 candles on Windows Python 3.15.0. All configured symbols and timeframes still require live doctor validation on that host.

## Toobit adapter

User confirmed direct server-time and 300 BTC-SWAP-USDT five-minute candles on Windows Python 3.15.0. Live rows had zero close-time values. New mocked tests cover derived close times, unfinished candle exclusion, symbol mapping, all three timeframes, cache, contract eligibility, and API error payload rejection. Full live four-symbol snapshot must still be checked on the user host.
