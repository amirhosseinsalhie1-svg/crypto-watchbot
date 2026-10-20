"""Compare the existing setups on native 5m and aggregated 15m Binance candles.

The 15m experiment aggregates only complete, contiguous groups of three 5m bars.
It reuses the existing research engine in an isolated process and temporarily
maps its internal execution-bar interval to 15m. Live scanner defaults are untouched.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import pandas as pd

from watchbot.core import Config, INTERVALS, iso, parse_time
from watchbot.backtest import run_backtest


SETUPS = {
    "trend-pullback": "config.trend-pullback.research.json",
    "range-breakout-v1": "config.range-breakout.json",
    "price-action-candlestick-v1": "config.price-action-candlestick.json",
}
BASE_5M_MS = 300_000
BASE_15M_MS = 900_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate_15m(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Build UTC 15m bars; reject incomplete or non-contiguous 5m triplets."""
    d = frame.copy()
    d["bucket"] = (d["open_time"].astype("int64") // BASE_15M_MS) * BASE_15M_MS
    rows = []
    for bucket, group in d.groupby("bucket", sort=True):
        group = group.sort_values("open_time")
        expected = [bucket, bucket + BASE_5M_MS, bucket + 2 * BASE_5M_MS]
        if len(group) != 3 or group["open_time"].astype("int64").tolist() != expected:
            continue
        if group["close_time"].astype("int64").tolist() != [t + BASE_5M_MS - 1 for t in expected]:
            continue
        rows.append({
            "open_time": int(bucket),
            "open": float(group.iloc[0]["open"]),
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "close": float(group.iloc[-1]["close"]),
            "volume": float(group["volume"].sum()),
            "close_time": int(bucket + BASE_15M_MS - 1),
        })
    result = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time"
    ])
    if result.empty:
        raise ValueError(f"No complete 15m candles for {symbol}")
    return result


def write_15m_dataset(source: Path, dest: Path, symbols: tuple[str, ...]) -> str:
    dest.mkdir(parents=True, exist_ok=True)
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf8"))
    files = []
    for symbol in symbols:
        for tf in ("4h", "1d"):
            src = source / f"{symbol}_{tf}.csv"
            target = dest / src.name
            shutil.copyfile(src, target)
            files.append({"name": target.name, "sha256": digest(target)})
        raw = pd.read_csv(source / f"{symbol}_5m.csv")
        bars = aggregate_15m(raw, symbol)
        target = dest / f"{symbol}_5m.csv"
        bars.to_csv(target, index=False)
        files.append({"name": target.name, "sha256": digest(target)})
    manifest = {
        "schema_version": 1,
        "source": source_manifest["source"],
        "derived_timeframe": "15m",
        "aggregation": "UTC contiguous groups of exactly three finalized 5m candles",
        "source_manifest_sha256": digest(source / "manifest.json"),
        "files": files,
    }
    path = dest / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    return digest(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    start, end = parse_time(args.start), parse_time(args.end)
    source = Path(args.data)
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    original_interval = INTERVALS["5m"]
    if original_interval != BASE_5M_MS:
        raise RuntimeError("Expected the normal 5m execution interval")
    comparisons = {}

    # 5m baseline, then a faithful 15m aggregation with equivalent wall-clock
    # max hold (4h) and cooldown (30m). Each run gets a fresh isolated journal.
    for timeframe in ("5m", "15m"):
        with tempfile.TemporaryDirectory(prefix=f"watchbot-{timeframe}-") as tmp:
            dataset = source
            manifest_hash = digest(source / "manifest.json")
            if timeframe == "15m":
                derived = Path(tmp) / "data"
                symbols = tuple(json.loads(
                    Path(SETUPS["trend-pullback"]).read_text(encoding="utf8")
                )["symbols"])
                manifest_hash = write_15m_dataset(source, derived, symbols)
                dataset = derived
                INTERVALS["5m"] = BASE_15M_MS
            try:
                for setup_id, config_path in SETUPS.items():
                    base = Config.load(config_path)
                    run_root = destination / timeframe / setup_id
                    cfg = replace(
                        base,
                        database=str(run_root / "journal.sqlite3"),
                        output_dir=str(run_root / "charts"),
                        max_hold_bars=48 if timeframe == "5m" else 16,
                        cooldown_bars=6 if timeframe == "5m" else 2,
                        entry_windows=("10:00-14:00", "16:00-20:00", "22:00-01:00"),
                    )
                    report = run_backtest(
                        cfg, str(dataset), start, end, str(run_root / "report-dir"),
                        ignore_news=True, progress=True,
                    )
                    report["research_timeframe"] = timeframe
                    report["execution"]["bar_interval"] = timeframe
                    report["execution"]["max_hold_wall_clock_minutes"] = 240
                    report["execution"]["cooldown_wall_clock_minutes"] = 30
                    report["source_dataset_manifest_sha256"] = manifest_hash
                    report_path = run_root / "report-dir" / "report.json"
                    report_path.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
                        encoding="utf8",
                    )
                    comparisons[f"{timeframe}:{setup_id}"] = {
                        "closed_trades": report["summary"]["closed_trades"],
                        "win_rate": report["summary"]["win_rate"],
                        "net_pnl_usdt": report["summary"]["net_pnl_usdt"],
                        "expectancy_net_r_per_planned_risk":
                            report["summary"]["expectancy_net_r_per_planned_risk"],
                        "profit_factor": report["summary"]["profit_factor"],
                        "marked_net_return_pct": report["marked_net_return_pct"],
                        "max_close_marked_drawdown_pct":
                            report["max_5m_close_marked_drawdown_pct"],
                        "open_positions": report["summary"]["open_positions"],
                        "report": str(report_path),
                    }
            finally:
                if timeframe == "15m":
                    INTERVALS["5m"] = original_interval

    result = {
        "kind": "same-rules-5m-vs-15m-research",
        "start_inclusive": iso(start),
        "end_exclusive": iso(end),
        "assumptions": {
            "symbols": list(Config.load(SETUPS["trend-pullback"]).symbols),
            "costs_and_risk": "unchanged from the setup research configs",
            "holding_horizon": "4 hours on both timeframes",
            "cooldown": "30 minutes on both timeframes",
            "entry_windows_tehran": ["10:00-14:00", "16:00-20:00", "22:00-01:00"],
            "open_positions_monitored": "24/7; entry windows gate new entries only",
            "15m_source": "complete UTC groups of three Binance 5m candles",
            "news": "ignored for research on both sides",
            "live_scanner_changed": False,
        },
        "results": comparisons,
        "limitations": [
            "Short sample; not evidence of profitability",
            "15m candles are aggregated from finalized 5m archive bars",
            "OHLC ambiguity, estimated costs, and archive revision limitations apply",
        ],
    }
    (destination / "timeframe-comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
