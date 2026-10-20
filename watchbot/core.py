from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import re

import numpy as np
import pandas as pd

INTERVALS = {"5m": 300_000, "4h": 14_400_000, "1d": 86_400_000}
VERSION = "pa-ichimoku-ema-v0.1"


def utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def parse_time(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must have a timezone")
    return int(dt.timestamp() * 1000)


@dataclass
class Config:
    symbols: tuple[str, ...] = ("SOLUSDT", "ORDIUSDT", "LINKUSDT", "BTCUSDT")
    source: str = "binance-usdm-reference"
    database: str = "runtime/journal.sqlite3"
    output_dir: str = "runtime/charts"
    news_file: str = "news.json"
    equity: float = 152.37
    risk_fraction: float = .0025
    daily_loss_fraction: float = .0075
    fee_bps_per_side: float = 5
    slippage_bps_per_side: float = 2
    funding_bps_per_8h: float = 3
    paper_enabled: bool = False
    reward_risk: float = 2
    max_hold_bars: int = 48
    cooldown_bars: int = 6
    max_signal_age_seconds: int = 45
    max_entry_gap_fraction: float = .003
    min_stop_fraction: float = .001
    max_stop_fraction: float = .03
    poll_seconds: int = 15
    history_bars: int = 300
    max_paper_notional_multiple: float = 1

    def __post_init__(self):
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Symbols must be nonempty and unique")
        if any(not re.fullmatch(r"[A-Z0-9]{3,24}USDT", s) for s in self.symbols):
            raise ValueError("Invalid USDT symbol")
        numeric = [getattr(self, f.name) for f in fields(self)
                   if isinstance(getattr(self, f.name), (int, float))]
        if any(not math.isfinite(x) for x in numeric):
            raise ValueError("Non-finite configuration")
        if type(self.paper_enabled) is not bool:
            raise ValueError("paper_enabled must be a JSON boolean")
        for name in ["max_hold_bars", "cooldown_bars", "max_signal_age_seconds", "poll_seconds", "history_bars"]:
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if not (0 < self.risk_fraction <= .01 and 0 < self.daily_loss_fraction <= .03):
            raise ValueError("Risk controls exceed research limits")
        if self.equity <= 0 or self.reward_risk < 1 or self.reward_risk > 10:
            raise ValueError("Invalid equity or reward/risk")
        if not (0 < self.min_stop_fraction < self.max_stop_fraction <= .1):
            raise ValueError("Invalid stop bounds")
        if not (0 < self.max_paper_notional_multiple <= 2):
            raise ValueError("Paper notional cap must be <= 2x equity")
        if any(x < 0 for x in [self.fee_bps_per_side, self.slippage_bps_per_side,
                              self.funding_bps_per_8h, self.max_entry_gap_fraction]):
            raise ValueError("Negative cost or gap assumption")
        if not (200 <= self.history_bars <= 1000 and 5 <= self.poll_seconds <= 60):
            raise ValueError("Invalid history or polling settings")
        if not (1 <= self.max_hold_bars <= 288 and self.cooldown_bars >= 1
                and 1 <= self.max_signal_age_seconds <= 120):
            raise ValueError("Invalid lifecycle settings")

    @classmethod
    def load(cls, path: str) -> "Config":
        raw = json.loads(Path(path).read_text(encoding="utf8"))
        if "symbols" in raw:
            raw["symbols"] = tuple(raw["symbols"])
        return cls(**raw)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


def validate_frame(df: pd.DataFrame, interval: str, cutoff: int,
                   *, fresh: bool = True, min_bars: int = 200) -> pd.DataFrame:
    """Only finalized, contiguous UTC bars ending before cutoff; no silent repairs."""
    duration = INTERVALS[interval]
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
    if not set(cols) <= set(df.columns):
        raise ValueError("Missing OHLCV/time columns")
    d = df[cols].copy()
    for c in cols:
        d[c] = pd.to_numeric(d[c], errors="raise")
    if not np.isfinite(d.to_numpy(dtype=float)).all():
        raise ValueError("Non-finite candle")
    if ((d.open_time % 1 != 0) | (d.close_time % 1 != 0)).any():
        raise ValueError("Timestamps must be integer UTC milliseconds")
    if ((d.close_time - d.open_time) != duration - 1).any():
        raise ValueError("Incorrect candle duration")
    if (d.open_time % duration != 0).any():
        raise ValueError("Candle is not on UTC interval boundary")
    if ((d[["open", "high", "low", "close"]] <= 0).any().any()
            or (d.volume < 0).any()
            or (d.high < d[["open", "close", "low"]].max(axis=1)).any()
            or (d.low > d[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("Impossible OHLCV geometry")
    if d.open_time.duplicated().any() or not d.open_time.is_monotonic_increasing:
        raise ValueError("Duplicate or unsorted candles")
    d = d[d.close_time < cutoff].reset_index(drop=True)
    if len(d) < min_bars:
        raise ValueError(f"Need {min_bars} finalized {interval} candles")
    if not d.open_time.diff().dropna().eq(duration).all():
        raise ValueError("Gap in candle history")
    if fresh and int(d.iloc[-1].close_time) != cutoff // duration * duration - 1:
        raise ValueError(f"Stale {interval} candle")
    d.open_time = d.open_time.astype("int64")
    d.close_time = d.close_time.astype("int64")
    return d


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema50"] = d.close.ewm(span=50, adjust=False, min_periods=50).mean()
    midpoint = lambda n: (d.high.rolling(n).max() + d.low.rolling(n).min()) / 2
    d["tenkan"] = midpoint(9)
    d["kijun"] = midpoint(26)
    # Cloud visible at time t was calculated 26 bars earlier, never a future value.
    d["span_a"] = ((d.tenkan + d.kijun) / 2).shift(26)
    d["span_b"] = midpoint(52).shift(26)
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(),
                    (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14).mean()  # simple TR mean, explicitly not Wilder ATR
    return d


def news_gate(path: str, symbol: str, now: int) -> tuple[str, str]:
    """Manual, expiring source review. Absence of a feed is NOT 'no news'."""
    try:
        n = json.loads(Path(path).read_text(encoding="utf8"))
        reviewed, until = parse_time(n["reviewed_at"]), parse_time(n["valid_until"])
        if reviewed > now or until <= now or until - reviewed > 6 * 3_600_000:
            return "UNKNOWN", "بازبینی خبر منقضی یا نامعتبر است"
        if not n["sources"] or not all(isinstance(s, str) and s.startswith("https://")
                                       for s in n["sources"]):
            return "UNKNOWN", "منبع معتبر برای بازبینی ثبت نشده"
        if symbol in n.get("blocked_symbols", []) or "*" in n.get("blocked_symbols", []):
            return "BLOCKED", "نماد در توقف دستی بررسی خبر است"
        for event in n.get("events", []):
            start, end = parse_time(event["start"]), parse_time(event["end"])
            if end < start:
                raise ValueError("Invalid event range")
            affected = event.get("symbols", ["*"])
            if start <= now <= end and (symbol in affected or "*" in affected):
                return "BLOCKED", str(event.get("name", "رویداد مهم"))
        return "CLEAR_REVIEWED", "بازبینی دستی معتبر؛ تضمین نبود خبر ناگهانی نیست"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return "UNKNOWN", "وضعیت خبر نامشخص؛ ورود فرضی متوقف است"


@dataclass
class Analysis:
    symbol: str
    source: str
    asof: int
    status: str
    side: str | None
    reasons: list[str]
    counterevidence: list[str]
    news_status: str
    news_note: str
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    signal_id: str | None = None
    strategy: str = VERSION
    config_hash: str = ""
    news_evidence: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def analyze(symbol: str, frames: dict[str, pd.DataFrame], now: int,
            cfg: Config, *, historical: bool = False) -> tuple[Analysis, dict]:
    news, note = news_gate(cfg.news_file, symbol, now)
    a = Analysis(symbol, cfg.source, now, "BLOCKED_DATA", None, [], [], news, note,
                 config_hash=cfg.fingerprint())
    try:
        raw_news = Path(cfg.news_file).read_bytes()
        parsed_news = json.loads(raw_news)
        a.news_evidence = {"sha256": hashlib.sha256(raw_news).hexdigest(),
                           "reviewed_at": parsed_news.get("reviewed_at"),
                           "valid_until": parsed_news.get("valid_until"),
                           "sources": parsed_news.get("sources", [])}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        f = {tf: indicators(validate_frame(frames[tf], tf, now, fresh=True))
             for tf in INTERVALS}
    except (ValueError, KeyError) as exc:
        a.reasons = [str(exc)]
        return a, {}
    daily, h4, m5 = (f[t].iloc[-1] for t in ["1d", "4h", "5m"])
    a.asof = int(m5.close_time) + 1
    if not historical and now - a.asof > cfg.max_signal_age_seconds * 1000:
        a.reasons = ["آخرین کندل برای صدور هشدار ورود بیش از حد قدیمی است"]
        return a, f
    a.status = "NO_SETUP"
    direction = (1 if daily.close > daily.ema50 and daily.tenkan > daily.kijun else
                 -1 if daily.close < daily.ema50 and daily.tenkan < daily.kijun else 0)
    if not direction:
        a.reasons = ["فیلتر روزانه همسو نیست"]
        a.counterevidence = ["جهت روزانه مختلط؛ منتظر می‌مانیم"]
        return a, f
    a.side = "LONG" if direction == 1 else "SHORT"
    a.reasons.append("قیمت، EMA50 و تنکان/کیجون روزانه همسو هستند")
    cloud_edge = max(h4.span_a, h4.span_b) if direction == 1 else min(h4.span_a, h4.span_b)
    h4_ok = (direction * (h4.close - h4.ema50) > 0
             and direction * (h4.close - cloud_edge) > 0
             and direction * (h4.tenkan - h4.kijun) > 0)
    if not h4_ok:
        a.counterevidence = ["تأیید ابر، EMA50 و تنکان/کیجون در ۴ساعته کامل نیست"]
        return a, f
    a.reasons.append("فیلتر ۴ساعته خارج ابر و همسو است")
    a.status = "PENDING"
    prev = f["5m"].iloc[-6:-1]
    pullback = ((prev.low <= prev.ema50 + .2 * prev.atr14).any() if direction == 1 else
                (prev.high >= prev.ema50 - .2 * prev.atr14).any())
    level = prev.iloc[-3:].high.max() if direction == 1 else prev.iloc[-3:].low.min()
    trigger = (pullback and direction * (m5.close - m5.open) > 0
               and direction * (m5.close - m5.ema50) > 0
               and direction * (m5.close - level) > 0)
    if not trigger:
        a.counterevidence = ["پولبک به EMA و شکست ساختار سه کندل قبل در ۵دقیقه تأیید نشده"]
        return a, f
    stop = ((min(prev.low.min(), m5.low) - .1 * m5.atr14) if direction == 1 else
            (max(prev.high.max(), m5.high) + .1 * m5.atr14))
    entry = float(m5.close)
    risk = direction * (entry - stop)
    if stop <= 0 or not cfg.min_stop_fraction <= risk / entry <= cfg.max_stop_fraction:
        a.status = "BLOCKED_RISK"
        a.counterevidence = ["فاصله ابطال خارج محدوده آزمایشی است"]
        return a, f
    a.entry, a.stop = entry, float(stop)
    a.target = entry + direction * cfg.reward_risk * risk
    a.status = "TECHNICAL_CONFIRMED"
    a.reasons.append("پولبک و شکست سه کندل قبل با کندل بسته‌شده ۵دقیقه تأیید شد")
    a.counterevidence = ["شکست کاذب و تغییر روند ممکن است؛ این قواعد هنوز اثبات سودآوری ندارند"]
    key = f"{VERSION}:{cfg.fingerprint()}:{cfg.source}:{symbol}:{a.asof}:{a.side}"
    a.signal_id = hashlib.sha256(key.encode()).hexdigest()[:24]
    return a, f
