"""Run one fixed-risk ten-symbol backtest at a selected supported leverage."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from .backtest import run_backtest
from .core import Config, parse_time


def build_config(config_path: str, output: str, leverage: int) -> Config:
    if type(leverage) is not int or not 10 <= leverage <= 20:
        raise ValueError("Research leverage must be an integer from 10 through 20")
    base = Config.load(config_path)
    if len(base.symbols) != 10:
        raise ValueError("Leverage sweep requires the established ten-symbol portfolio")
    if base.max_open_positions != 2 or base.min_net_reward_risk < 3:
        raise ValueError("Leverage sweep requires at most two positions and net R:R >= 3")
    if not base.paper_enabled:
        raise ValueError("Leverage sweep requires paper_enabled=true")
    destination = Path(output)
    return replace(
        base,
        paper_leverage=leverage,
        database=str(destination / "journal.sqlite3"),
        output_dir=str(destination / "charts"),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fixed-risk 10x/15x/20x portfolio research")
    parser.add_argument("--config", default="config.portfolio-research.json")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--leverage", type=int, required=True, choices=(10, 15, 20))
    args = parser.parse_args(argv)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cfg = build_config(args.config, str(output), args.leverage)
    report = run_backtest(
        cfg,
        args.data,
        parse_time(args.start),
        parse_time(args.end),
        str(output),
        ignore_news=True,
        progress=True,
    )
    summary = report["summary"]
    row = {
        "leverage": args.leverage,
        "symbols": list(cfg.symbols),
        "start_inclusive": report["start_inclusive"],
        "end_exclusive": report["end_exclusive"],
        "closed_trades": summary["closed_trades"],
        "win_rate_pct": round((summary["win_rate"] or 0) * 100, 2),
        "net_pnl_usdt": summary["closed_trade_pnl_usdt"],
        "expectancy_net_r": summary["expectancy_net_r_per_planned_risk"],
        "profit_factor": summary["profit_factor"],
        "max_drawdown_pct": report["max_5m_close_marked_drawdown_pct"],
        "open_positions_at_end": summary["open_positions"],
        "marked_net_return_pct": report["marked_net_return_pct"],
        "max_consecutive_losses": report["max_consecutive_closed_losses"],
        "dataset_manifest_sha256": report["dataset_manifest_sha256"],
        "risk_fraction": cfg.risk_fraction,
        "max_open_positions": cfg.max_open_positions,
        "minimum_net_reward_risk": cfg.min_net_reward_risk,
        "max_paper_notional_multiple": cfg.max_paper_notional_multiple,
        "note": "Leverage is a margin setting; position risk sizing is held constant. No liquidation model.",
    }
    (output / "comparison-row.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8"
    )
    print(json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
