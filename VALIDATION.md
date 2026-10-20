# Validation status — 0.4.0 — 2026-10-20

- Python unit/integration tests: 70 passed in the project virtual environment. Fourteen JavaScript model tests passed.
- Dashboard checks cover read-only snapshots, immutable historical inputs, legacy rows, control queue and scanner acknowledgement, host/origin/session checks, observation-time replay boundaries, alert expiry/invalidation and evidence-grounded responses.
- Browser-based visual QA was not performed in this build environment. Responsive layout and canvas interactions still need validation on a real target browser.
- Python wheel packaging: successfully built locally with no dependency downloads.
- Offline demo: generated four three-timeframe PNG charts with synthetic LONG and SHORT confirmation fixtures, Persian captions and JSON evidence.
- Transport tests: mocked Binance OHLC responses, cache behavior, clock-skew rejection, Telegram multipart photo payload and secret-redacted HTTP errors.
- Paper tests: next-bar entry, adverse slippage and costs, stop-first ambiguous candle, gap loss, entry-gap cancellation, one-position limit, news gating, restart idempotency, risk stops, Tehran day boundary, transaction rollback and timeout.
- No look-ahead checks: unfinished candle exclusion, displaced Ichimoku cloud, future reversal exclusion in walk-forward processing.
- Diagnostics regression checks: offline preflight never uses the network or creates a journal; partial credentials fail with redacted output; public-data probe never calls Telegram; failed/incomplete/boundary-crossed snapshots are rejected before analysis and heartbeat updates; CLI returns a failure exit code when appropriate.
- CLI walk-forward smoke test: a deliberately constructed synthetic dataset went through pending entry, fill, exit and CSV export. It is not evidence about real prices or returns.
- Current full REST preflight still returns **HTTP 451** for Binance time/exchangeInfo, so `market_verified` is false and full scanner operation is not verified. A valid public WebSocket kline message was received, validated and read as FRESH through the quote-only CLI plus the local `/api/market` endpoint. That smoke test created no analysis database and sent no Telegram message. No alternate unofficial endpoint was used.
- Telegram delivery to a real user: **not tested**, no token/destination configured during development.
- Docker configuration supplied; Docker execution and Windows timezone behavior have not been verified locally. GitHub Actions execution status is available separately in the repository's Actions tab.
- A limited real-data research comparison is supplied separately: BTCUSDT/SOLUSDT, Binance USD-M, 2026-09-14 inclusive through 2026-09-21 exclusive, 30 checksum-verified monthly archives, four predeclared variants. Costs are estimated and historical news is explicitly omitted. The sample does not establish profitability; no live trading or reliability guarantee is claimed. Synthetic fixtures verify behavior independently of the financial results.

Run `python -m unittest discover -s tests -v` after installation. Deployment requires a host that can access the selected public data provider legitimately, a private Telegram bot token, an explicit destination and durable storage.

Research regression tests cover session boundaries/overnight windows, exits outside session, exact partial fill cash/cost accounting, conservative TP1/BE ambiguity, first-loss drawdown, gap fills, open trade marking, insufficient warm-up, future candle invariance, dataset tampering, archive header handling and a complete bounded synthetic replay. Live-display tests reject stale/out-of-order/invalid data, config mismatches and chart gaps, and exclude mutable quotes from imported evidence and replay. Frame-cache tests preserve uncached results and detect revised or stale data.
