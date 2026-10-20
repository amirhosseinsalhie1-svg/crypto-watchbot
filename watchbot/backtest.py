"""Bounded walk-forward backtests using the same scanner and paper ledger."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

from .core import Config, INTERVALS, iso, validate_frame
from .engine import Engine, single_instance
from .io import load_csv


def marked_equity(journal, prices: dict[str, float], at: int) -> float:
    """Estimated liquidation equity at finalized closes, including remaining costs."""
    value = journal.get("equity")
    states = journal.get("position_states", {})
    for p in journal.db.execute("SELECT * FROM trades WHERE exit_at IS NULL"):
        state = states.get(p["id"], {"remaining_qty": p["qty"]})
        qty = state["remaining_qty"]
        direction = 1 if p["side"] == "LONG" else -1
        exit_price = prices[p["symbol"]] * (1 - direction * journal.cfg.slippage_bps_per_side / 10_000)
        fees = (p["entry"] + exit_price) * qty * journal.cfg.fee_bps_per_side / 10_000
        funding = p["entry"] * qty * journal.cfg.funding_bps_per_8h / 10_000 * max(0, at - p["entry_at"]) / 28_800_000
        value += direction * (exit_price - p["entry"]) * qty - fees - funding
    return value


def run_backtest(cfg: Config, folder: str, start: int, end: int, output: str,
                 *, ignore_news: bool = False, progress: bool = False) -> dict:
    if not cfg.paper_enabled:
        raise ValueError("Backtest needs paper_enabled=true in a separate experiment config")
    duration = INTERVALS["5m"]
    if not start < end or end - start > 31 * INTERVALS["1d"] or start % duration or end % duration:
        raise ValueError("Use aligned UTC boundaries and a range of at most 31 days")
    if cfg.strategy_profile == "guarded-v1" and cfg.history_bars < 250:
        raise ValueError("Guarded profile needs at least 250 history bars")
    root, destination = Path(folder), Path(output)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("source") != cfg.source:
        raise ValueError("Archive manifest/source mismatch")
    expected = {r["name"]: r["sha256"] for r in manifest["files"]}
    for symbol in cfg.symbols:
        for tf in INTERVALS:
            path = root / f"{symbol}_{tf}.csv"
            if expected.get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError(f"Dataset checksum mismatch: {path.name}")
    snapshots = {s: load_csv(root, s) for s in cfg.symbols}
    for s, frames in snapshots.items():
        for tf, data in frames.items():
            frames[tf] = validate_frame(data, tf, end, fresh=False)
            validate_frame(data, tf, start, fresh=True, min_bars=cfg.history_bars)
            if int(frames[tf].iloc[-1].close_time) != end // INTERVALS[tf] * INTERVALS[tf] - 1:
                raise ValueError(f"Dataset does not cover the complete experiment: {s}/{tf}")
    destination.mkdir(parents=True, exist_ok=True)
    equity_rows = [{"at": start, "equity_usdt": cfg.equity}]
    peak, max_dd = cfg.equity, 0
    with single_instance(cfg.database):
        engine = Engine(cfg)
        j = engine.journal
        try:
            if j.db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] or j.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]:
                raise ValueError("Backtest requires a fresh experiment database")
            with j.transaction():
                j.event(start, "BACKTEST_START", {"start": start, "end": end, "ignore_news": ignore_news})
            for now in range(start, end + 1, duration):
                sliced = {s: {tf: d.iloc[max(0, (n := int(d.close_time.searchsorted(now))) - cfg.history_bars):n]
                              for tf, d in frames.items()} for s, frames in snapshots.items()}
                engine.process(now, sliced, historical=True, ignore_news=ignore_news,
                               queue_alerts=False, render_signals=False)
                prices = {s: float(frames["5m"].iloc[-1].close) for s, frames in sliced.items()}
                equity = marked_equity(j, prices, now)
                peak = max(peak, equity)
                max_dd = max(max_dd, 1 - equity / peak)
                equity_rows.append({"at": now, "equity_usdt": equity})
                if progress and (now - start) % INTERVALS["1d"] == 0:
                    print(f"Backtest {iso(now)} | closed={j.summary()['closed_trades']}", file=sys.stderr, flush=True)
            with j.transaction():
                for pending in j._pending_orders():
                    j.event(end, "BACKTEST_PENDING_UNFILLED_AT_END", {"id": pending["signal_id"]})
                j.clear_pending()
                j.event(end, "BACKTEST_END", {"open_policy": "KEEP_OPEN_AND_MARK_AT_FINAL_CLOSE"})
            statuses = Counter(json.loads(r[0])["status"] for r in j.db.execute("SELECT payload FROM analyses"))
            by_symbol = {}
            max_streak = streak = 0
            for trade in j.db.execute("SELECT * FROM trades WHERE exit_at IS NOT NULL ORDER BY exit_at,entry_at"):
                streak = streak + 1 if trade["pnl"] < 0 else 0
                max_streak = max(max_streak, streak)
                record = by_symbol.setdefault(trade["symbol"], {"closed_trades": 0, "wins": 0, "net_pnl_usdt": 0})
                record["closed_trades"] += 1
                record["wins"] += int(trade["pnl"] > 0)
                record["net_pnl_usdt"] += trade["pnl"]
            report = {"schema_version": 1, "kind": "watchbot-bounded-backtest", "source": cfg.source,
                "start_inclusive": iso(start), "end_exclusive": iso(end),
                "news_policy": "UNSCREENED_RESEARCH" if ignore_news else "HISTORICAL_REVIEW_REQUIRED",
                "config": asdict(cfg), "summary": j.summary(), "by_symbol": by_symbol,
                "status_counts": dict(statuses), "max_consecutive_closed_losses": max_streak,
                "final_marked_equity_usdt": equity_rows[-1]["equity_usdt"],
                "marked_net_return_pct": 100 * (equity_rows[-1]["equity_usdt"] / cfg.equity - 1),
                "max_5m_close_marked_drawdown_pct": max_dd * 100,
                "dataset_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "execution": {"entry": "next candle open with adverse slippage; excessive gaps cancel",
                    "intrabar": "existing stop first; after TP1, breakeven wins an ambiguous same-bar retest",
                    "funding": "fixed adverse cost estimate, not historical realized funding",
                    "open_at_end": "reported and marked; never silently discarded",
                    "session": "entry gate only; monitoring and exits run 24h"},
                "limitations": ["Small historical research sample; not evidence of profitability",
                    "OHLC cannot reconstruct tick ordering or actual fills",
                    "Drawdown sampled at 5m closes; intrabar drawdown may be larger",
                    "Final archive candles; original publication latency and later archive revisions are not reconstructed",
                    "Fees/slippage/funding estimated; no order book or liquidation model",
                    "No statistically reliable Sharpe reported for this short experiment"]}
            j.export(str(destination / "ledger"))
            pd.DataFrame(equity_rows).to_csv(destination / "equity.csv", index=False)
            (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
            return report
        finally:
            j.close()
