"""Deterministic synthetic fixtures, never market evidence or historical performance."""
from dataclasses import replace
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .core import Config, INTERVALS, utc_ms
from .engine import Engine


def fixture(end: int, tf: str, base=100.0, count=300, *, short=False):
    duration = INTERVALS[tf]
    boundary = end // duration * duration
    times = np.arange(boundary - count * duration, boundary, duration, dtype="int64")
    close = np.linspace(base * .75, base, count)
    op = close - base * .0005
    d = pd.DataFrame({"open_time": times, "open": op,
                      "high": close + base * .001,
                      "low": op - base * .001, "close": close,
                      "volume": np.full(count, 1000.0), "close_time": times + duration - 1})
    if tf == "5m":
        # Five-bar pullback touches EMA, then final candle breaks preceding highs.
        for i in range(count - 6, count - 1):
            mid = base * .975 + (i - (count - 6)) * base * .0002
            d.loc[i, ["open", "high", "low", "close"]] = [mid, mid + base * .002,
                                                                       mid - base * .002, mid + base * .0005]
        d.loc[count - 1, ["open", "high", "low", "close"]] = [base * .978, base * 1.001,
                                                                            base * .974, base]
    if short:
        # Reflect all prices to produce mathematically symmetric bearish fixtures.
        old = d[["open", "high", "low", "close"]].copy()
        d["open"], d["close"] = 2 * base - old.open, 2 * base - old.close
        d["high"], d["low"] = 2 * base - old.low, 2 * base - old.high
    return d


def demo(cfg: Config, directory="runtime/demo"):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    now = utc_ms() // INTERVALS["5m"] * INTERVALS["5m"] + 1000
    demo_cfg = replace(cfg, source="SYNTHETIC_DEMO_NOT_MARKET_DATA", database=str(root / "demo.sqlite3"),
                       output_dir=str(root / "charts"), paper_enabled=False)
    data = {s: {tf: fixture(now, tf, base=b, short=s == "ORDIUSDT") for tf in INTERVALS}
            for s, b in zip(demo_cfg.symbols, [100, 30, 15, 80000])}
    for s, frames in data.items():
        for tf, frame in frames.items():
            frame.to_csv(root / f"{s}_{tf}.csv", index=False)
    engine = Engine(demo_cfg)
    try:
        reports = engine.process(now, data, render_all=True, queue_alerts=False)
        (root / "analysis.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf8")
        return {"synthetic": True, "statuses": {r["symbol"]: r["status"] for r in reports},
                "output": str(root.resolve())}
    finally:
        engine.journal.close()
