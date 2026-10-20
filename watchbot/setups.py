"""Additional deterministic setup evaluators.

All evaluators receive finalized, validated, indicator-enriched frames from core.
"""
from __future__ import annotations

import hashlib

from .core import Analysis, Config


def evaluate_range_breakout(a: Analysis, f: dict, now: int, cfg: Config, *,
                            historical: bool = False):
    """20-bar Donchian breakout with daily/4h trend and candle/volume guards.

    The close is the signal reference only. The journal still simulates entry at
    the next bar open. Existing journal execution controls all stops and targets.
    """
    daily = f["1d"].iloc[-1]
    h4 = f["4h"].iloc[-1]
    m5_frame = f["5m"]
    m5 = m5_frame.iloc[-1]
    a.asof = int(m5.close_time) + 1

    if not historical and now - a.asof > cfg.max_signal_age_seconds * 1000:
        a.status = "NO_SETUP"
        a.reasons = ["آخرین کندل برای صدور هشدار ورود بیش از حد قدیمی است"]
        return a, f

    daily_direction = (
        1 if daily.close > daily.ema50 and daily.tenkan > daily.kijun else
        -1 if daily.close < daily.ema50 and daily.tenkan < daily.kijun else 0
    )
    if not daily_direction:
        a.status = "NO_SETUP"
        a.reasons = ["جهت روزانه برای شکست محدوده همسو نیست"]
        a.counterevidence = ["قیمت و تنکان/کیجون روزانه جهت مشترک ندارند"]
        return a, f

    a.side = "LONG" if daily_direction == 1 else "SHORT"
    cloud_edge = max(h4.span_a, h4.span_b) if daily_direction == 1 else min(h4.span_a, h4.span_b)
    h4_aligned = (
        daily_direction * (h4.close - h4.ema50) > 0
        and daily_direction * (h4.close - cloud_edge) > 0
        and daily_direction * (h4.tenkan - h4.kijun) > 0
    )
    if not h4_aligned:
        a.status = "NO_SETUP"
        a.reasons = ["جهت روزانه مشخص است"]
        a.counterevidence = ["تأیید روند و ابر در ۴ساعته کامل نیست"]
        return a, f

    # Current bar is excluded from both range levels. Previous close must still
    # be inside the prior range, which prevents repeated signals after breakout.
    prior = m5_frame.iloc[-21:-1]
    before = m5_frame.iloc[-22:-2]
    if len(prior) != 20 or len(before) != 20:
        a.status = "BLOCKED_DATA"
        a.reasons = ["برای محاسبهٔ محدودهٔ ۲۰ کندلی تاریخچه کافی نیست"]
        return a, f

    prior_high = float(prior.high.max())
    prior_low = float(prior.low.min())
    before_high = float(before.high.max())
    before_low = float(before.low.min())
    previous_close = float(m5_frame.iloc[-2].close)

    if daily_direction == 1:
        level = prior_high
        fresh_break = previous_close <= before_high and m5.close > level
        directional_candle = m5.close > m5.open
        extension = m5.close - level
        stop = min(float(m5.low), level) - .1 * float(m5.atr14)
    else:
        level = prior_low
        fresh_break = previous_close >= before_low and m5.close < level
        directional_candle = m5.close < m5.open
        extension = level - m5.close
        stop = max(float(m5.high), level) + .1 * float(m5.atr14)

    a.status = "PENDING"
    a.reasons = ["روند روزانه و ۴ساعته همسو است"]
    failed = []
    if not fresh_break:
        failed.append("شکست تازهٔ سقف/کف ۲۰ کندل بسته‌شده تأیید نشده")
    if not directional_candle:
        failed.append("کندل شکست در جهت روند بسته نشده")
    if not (m5.body_ratio >= .60):
        failed.append("بدنهٔ کندل شکست کمتر از ۶۰٪ دامنه است")
    if not (m5.volume >= 1.2 * m5.prior_volume20):
        failed.append("حجم کندل شکست کمتر از ۱٫۲ برابر میانگین ۲۰ کندل قبلی است")
    if not (extension >= 0 and extension <= .5 * m5.atr14):
        failed.append("قیمت بیش از نیم ATR از سطح شکست امتداد یافته است")
    if failed:
        a.counterevidence = failed
        return a, f

    entry = float(m5.close)
    risk = daily_direction * (entry - stop)
    if stop <= 0 or not cfg.min_stop_fraction <= risk / entry <= cfg.max_stop_fraction:
        a.status = "BLOCKED_RISK"
        a.counterevidence = ["فاصلهٔ حد ضرر ساختاری خارج از محدودهٔ آزمایشی است"]
        return a, f

    a.entry = entry
    a.stop = float(stop)
    a.target = entry + daily_direction * cfg.reward_risk * risk
    a.status = "TECHNICAL_CONFIRMED"
    a.reasons.extend([
        "کندل ۵دقیقه بالای سقف ۲۰ کندل بسته شد" if daily_direction == 1
        else "کندل ۵دقیقه زیر کف ۲۰ کندل بسته شد",
        "بدنه و حجم کندل شکست از فیلترهای ازپیش‌تعریف‌شده عبور کردند",
    ])
    a.counterevidence = [
        "شکست کاذب، لغزش و تغییر رژیم بازار ممکن است؛ این ستاپ سودآوری تضمین‌شده ندارد"
    ]
    if a.entry_session == "MONITOR_ONLY":
        a.counterevidence.append("خارج از ساعت مجاز ورود؛ موقعیت باز همچنان مدیریت می‌شود")
    key = f"range-breakout-v1:{cfg.fingerprint()}:{cfg.source}:{a.symbol}:{a.asof}:{a.side}"
    a.signal_id = hashlib.sha256(key.encode()).hexdigest()[:24]
    return a, f


def register_additional_setups(register_setup):
    """Called once by core after its built-in evaluator has been registered."""
    register_setup("range-breakout-v1", evaluate_range_breakout)
