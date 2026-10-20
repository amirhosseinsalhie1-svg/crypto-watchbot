"""Predeclared four-arm research comparison; never downloads keys or sends orders."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchbot.backtest import run_backtest
from watchbot.core import Config, parse_time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--data", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "SOLUSDT"])
    args = p.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    base = replace(Config.load(args.config), symbols=tuple(args.symbols), paper_enabled=True)
    arms = [
        ("01-classic-24h", dict(strategy_profile="classic", entry_windows=(), tp1_close_fraction=0, reward_risk=2)),
        ("02-guards-24h", dict(strategy_profile="guarded-v1", entry_windows=(), tp1_close_fraction=0, reward_risk=2)),
        ("03-guards-staged-24h", dict(strategy_profile="guarded-v1", entry_windows=(), tp1_close_fraction=.5, reward_risk=3)),
        ("04-guards-staged-session", dict(strategy_profile="guarded-v1", entry_windows=("16:00-20:00",),
                                         tp1_close_fraction=.5, reward_risk=3)),
    ]
    rows = []
    for name, changes in arms:
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        cfg = replace(base, database=str(directory / "journal.sqlite3"),
                      output_dir=str(directory / "charts"), **changes)
        (directory / "config.json").write_text(json.dumps(asdict(cfg), indent=2), encoding="utf8")
        print(f"Starting {name}", flush=True)
        report = run_backtest(cfg, args.data, parse_time(args.start), parse_time(args.end),
                              str(directory), ignore_news=True, progress=True)
        summary = report["summary"]
        rows.append({"experiment": name, "closed_trades": summary["closed_trades"],
            "win_rate": summary["win_rate"], "profit_factor": summary["profit_factor"],
            "expectancy_net_r": summary["expectancy_net_r_per_planned_risk"],
            "final_marked_equity_usdt": report["final_marked_equity_usdt"],
            "marked_net_return_pct": report["marked_net_return_pct"],
            "max_5m_close_marked_drawdown_pct": report["max_5m_close_marked_drawdown_pct"],
            "open_positions": summary["open_positions"],
            "technical_confirmed": report["status_counts"].get("TECHNICAL_CONFIRMED", 0),
            "by_symbol": report["by_symbol"]})
        (root / "comparison.json").write_text(json.dumps(rows, indent=2, allow_nan=False), encoding="utf8")
        print(json.dumps(rows[-1], indent=2), flush=True)
    print("Comparison complete. News omitted explicitly; no parameter tuning or profitability claim.", flush=True)


if __name__ == "__main__":
    main()
