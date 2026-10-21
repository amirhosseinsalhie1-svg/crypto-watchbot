"""Read-only preflight checks: never enqueue alerts or open a journal."""
from __future__ import annotations

import importlib.metadata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import Config, INTERVALS, iso, news_gate, utc_ms, validate_frame
from .io import BinanceReference, MexcReference, NetworkError, Telegram


def validate_snapshot(cfg: Config, now: int, snapshots: dict) -> dict:
    """Require every configured symbol/timeframe at the final snapshot timestamp."""
    summary = {}
    for symbol in cfg.symbols:
        summary[symbol] = {}
        for tf in INTERVALS:
            try:
                frame = validate_frame(snapshots[symbol][tf], tf, now, fresh=True)
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Incomplete market snapshot: {symbol}/{tf}") from exc
            summary[symbol][tf] = {
                "closed_bars": len(frame),
                "last_close": iso(int(frame.iloc[-1].close_time) + 1),
            }
    return summary


def doctor(cfg: Config, *, live: bool = False) -> dict:
    """Live mode only reads the selected public market API; no Telegram requests."""
    checks = [{"name": "configuration", "status": "PASS",
               "detail": "Configuration valid; no real order execution path"}]
    for package in ("numpy", "pandas", "matplotlib", "websockets"):
        try:
            version = importlib.metadata.version(package)
            checks.append({"name": package, "status": "PASS", "detail": version})
        except importlib.metadata.PackageNotFoundError:
            checks.append({"name": package, "status": "FAIL", "detail": "Dependency not installed"})
    try:
        ZoneInfo("Asia/Tehran")
        checks.append({"name": "timezone", "status": "PASS", "detail": "Asia/Tehran available"})
    except ZoneInfoNotFoundError:
        checks.append({"name": "timezone", "status": "FAIL", "detail": "Install timezone database (tzdata)"})

    telegram = Telegram()
    if bool(telegram.token) != bool(telegram.chat):
        status, detail = "FAIL", "Set both Telegram environment variables or leave both empty"
    elif not telegram.configured:
        status, detail = "WARN", "Telegram disabled; local analysis remains available"
    elif telegram.chat.startswith("-") and not telegram.operator:
        status, detail = "WARN", "Group commands require TELEGRAM_OPERATOR_USER_ID"
    else:
        status, detail = "WARN", "Credentials present; actual delivery has not been verified"
    checks.append({"name": "telegram", "status": status, "detail": detail})

    now = utc_ms()
    news = {s: news_gate(cfg.news_file, s, now)[0] for s in cfg.symbols}
    checks.append({"name": "news", "status": "PASS" if all(
        status == "CLEAR_REVIEWED" for status in news.values()) else "WARN",
        "detail": news})
    market = None
    if cfg.source not in ("binance-usdm-reference", "mexc-usdt-perpetual"):
        checks.append({"name": "live_source", "status": "FAIL",
                       "detail": "Configured source supports CSV replay only"})
    elif not live:
        checks.append({"name": "market", "status": "WARN", "detail": "Not probed; use doctor --live"})
    else:
        try:
            provider = MexcReference(cfg) if cfg.source == "mexc-usdt-perpetual" else BinanceReference(cfg)
            missing = set(cfg.symbols) - provider.available_symbols()
            if missing:
                raise ValueError("Unavailable perpetual symbols: " + ", ".join(sorted(missing)))
            at, snapshots = provider.snapshot(cfg.symbols)
            market = validate_snapshot(cfg, at, snapshots)
            checks.append({"name": "market", "status": "PASS",
                           "detail": "All symbols/timeframes have current finalized candles"})
        except (NetworkError, ValueError, KeyError, TypeError, OSError):
            # Do not echo third-party exception strings, URLs, credentials or payloads.
            checks.append({"name": "market", "status": "FAIL",
                           "detail": "Public data probe failed; check provider access, symbols, clock and candle history"})
    return {"mode": "PREFLIGHT_ONLY", "checked_at": iso(now), "checks": checks,
            "ok": not any(c["status"] == "FAIL" for c in checks),
            "market_verified": market is not None, "market": market,
            "telegram_delivery_verified": False, "paper_enabled": cfg.paper_enabled}
