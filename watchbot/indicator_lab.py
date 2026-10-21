"""Signal-free indicator study for finalized Binance 5m candles.

This module writes indicator features and forward close-to-close observations. It
never emits entries, trades, recommendations, or paper fills.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .core import Config, INTERVALS, iso, validate_frame
from .io import load_csv

FEATURE_COLUMNS = (
    "ema9_gap_pct", "ema21_gap_pct", "ema9_ema21_gap_pct",
    "rsi14", "tsi25_13", "tsi_signal13", "tsi_hist",
    "macd_line_pct", "macd_signal_pct", "macd_hist_pct",
    "vwap20_gap_pct", "bb20_percent_b", "bb20_width_pct",
    "atr14_pct", "volume_sma20_ratio",
)
HORIZONS = (1, 3, 6)


def add_candidate_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add common scalp-study features using current and prior finalized bars only."""
    d = frame.copy()
    close = d["close"].astype(float)
    high, low = d["high"].astype(float), d["low"].astype(float)
    volume = d["volume"].astype(float)

    ema9 = close.ewm(span=9, adjust=False, min_periods=9).mean()
    ema21 = close.ewm(span=21, adjust=False, min_periods=21).mean()
    d["ema9_gap_pct"] = (close / ema9 - 1) * 100
    d["ema21_gap_pct"] = (close / ema21 - 1) * 100
    d["ema9_ema21_gap_pct"] = (ema9 / ema21 - 1) * 100

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
    d["rsi14"] = rsi

    momentum = close.diff()
    tsi_numerator = momentum.ewm(span=25, adjust=False, min_periods=25).mean().ewm(
        span=13, adjust=False, min_periods=13
    ).mean()
    tsi_denominator = momentum.abs().ewm(span=25, adjust=False, min_periods=25).mean().ewm(
        span=13, adjust=False, min_periods=13
    ).mean().replace(0, np.nan)
    tsi = 100 * tsi_numerator / tsi_denominator
    tsi_signal = tsi.ewm(span=13, adjust=False, min_periods=13).mean()
    d["tsi25_13"] = tsi
    d["tsi_signal13"] = tsi_signal
    d["tsi_hist"] = tsi - tsi_signal

    macd = close.ewm(span=12, adjust=False, min_periods=26).mean() - close.ewm(
        span=26, adjust=False, min_periods=26
    ).mean()
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    d["macd_line_pct"] = macd / close * 100
    d["macd_signal_pct"] = macd_signal / close * 100
    d["macd_hist_pct"] = (macd - macd_signal) / close * 100

    typical = (high + low + close) / 3
    vwap_denominator = volume.rolling(20, min_periods=20).sum().replace(0, np.nan)
    vwap20 = (typical * volume).rolling(20, min_periods=20).sum() / vwap_denominator
    d["vwap20_gap_pct"] = (close / vwap20 - 1) * 100

    middle = close.rolling(20, min_periods=20).mean()
    deviation = close.rolling(20, min_periods=20).std(ddof=0)
    upper, lower = middle + 2 * deviation, middle - 2 * deviation
    width = upper - lower
    d["bb20_percent_b"] = (close - lower) / width.replace(0, np.nan)
    d["bb20_width_pct"] = width / middle * 100

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    d["atr14_pct"] = atr / close * 100

    volume_mean = volume.rolling(20, min_periods=20).mean().replace(0, np.nan)
    d["volume_sma20_ratio"] = volume / volume_mean
    return d


def _load_verified_5m(cfg: Config, root: Path, symbol: str, end: int) -> pd.DataFrame:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("source") != cfg.source or cfg.source != "binance-usdm-reference":
        raise ValueError("Indicator study requires an official Binance USD-M archive manifest")
    expected = {item["name"]: item["sha256"] for item in manifest.get("files", [])}
    path = root / f"{symbol}_5m.csv"
    if expected.get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError(f"Dataset checksum mismatch: {path.name}")
    data = load_csv(root, symbol)["5m"]
    return validate_frame(data, "5m", end, fresh=False, min_bars=cfg.history_bars)


def run_indicator_study(cfg: Config, folder: str, start: int, end: int, output: str) -> dict:
    """Create a reproducible, non-trading indicator feature/outcome study."""
    step = INTERVALS["5m"]
    if not start < end or end - start > 31 * INTERVALS["1d"] or start % step or end % step:
        raise ValueError("Use aligned UTC boundaries and a range of at most 31 days")
    root, destination = Path(folder), Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    for symbol in cfg.symbols:
        data = _load_verified_5m(cfg, root, symbol, end)
        validate_frame(data, "5m", start, fresh=True, min_bars=cfg.history_bars)
        expected_last = end // step * step - 1
        if int(data.iloc[-1].close_time) != expected_last:
            raise ValueError(f"Dataset does not cover the complete range: {symbol}/5m")
        featured = add_candidate_features(data)
        in_range = (featured.open_time >= start) & (featured.open_time < end)
        positions = np.flatnonzero(in_range.to_numpy())
        for horizon in HORIZONS:
            targets = positions + horizon
            valid = targets < len(featured)
            positions_h, targets_h = positions[valid], targets[valid]
            # A target candle at or beyond the exclusive end belongs outside this study.
            valid_end = featured.iloc[targets_h].open_time.to_numpy() < end
            positions_h, targets_h = positions_h[valid_end], targets_h[valid_end]
            part = featured.iloc[positions_h][["open_time", "close_time", *FEATURE_COLUMNS]].copy()
            part.insert(0, "symbol", symbol)
            part["horizon_bars"] = horizon
            future_close = featured.iloc[targets_h].close.to_numpy(dtype=float)
            current_close = featured.iloc[positions_h].close.to_numpy(dtype=float)
            part["future_gross_return_pct"] = (future_close / current_close - 1) * 100
            frames.append(part)

    if not frames:
        raise ValueError("No eligible 5m observations found in the requested range")
    observations = pd.concat(frames, ignore_index=True)
    observations = observations.replace([np.inf, -np.inf], np.nan)
    observations.to_csv(destination / "observations.csv", index=False)

    rows = []
    for horizon in HORIZONS:
        subset = observations[observations.horizon_bars == horizon]
        for feature in FEATURE_COLUMNS:
            valid = subset[[feature, "future_gross_return_pct"]].dropna()
            correlation = valid[feature].corr(valid["future_gross_return_pct"], method="spearman")
            rows.append({
                "horizon_bars": horizon,
                "feature": feature,
                "observations": int(len(valid)),
                "spearman_rho": None if pd.isna(correlation) else float(correlation),
                "mean_future_gross_return_pct": (
                    float(valid["future_gross_return_pct"].mean()) if len(valid) else None
                ),
            })

    report = {
        "schema_version": 1,
        "kind": "watchbot-signal-free-indicator-study",
        "source": cfg.source,
        "start_inclusive": iso(start),
        "end_exclusive": iso(end),
        "symbols": list(cfg.symbols),
        "timeframe": "5m",
        "indicator_features": list(FEATURE_COLUMNS),
        "forward_horizons_bars": list(HORIZONS),
        "observation_rows": int(len(observations)),
        "feature_associations": rows,
        "limitations": [
            "Descriptive feature/outcome study only; it does not create or backtest trades",
            "Forward returns are gross close-to-close and exclude fees, slippage, funding, and execution delay",
            "Correlations are exploratory, not proof of predictive value or profitability",
            "Indicators use finalized bars; forward returns are labels only and are not inputs to features",
        ],
        "dataset_manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
    }
    (destination / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8"
    )
    return report
