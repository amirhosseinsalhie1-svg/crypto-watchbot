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
    candle_range = float(m5.high - m5.low)
    close_location = (float(m5.close) - float(m5.low)) / candle_range if candle_range > 0 else .5
    close_near_edge = close_location >= .75 if daily_direction == 1 else close_location <= .25

    failed = []
    if not fresh_break:
        failed.append("شکست تازهٔ سقف/کف ۲۰ کندل بسته‌شده تأیید نشده")
    if not directional_candle:
        failed.append("کندل شکست در جهت روند بسته نشده")
    if not close_near_edge:
        failed.append("کندل شکست در یک‌چهارم انتهایی دامنه بسته نشده")
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



def evaluate_price_action_candlestick(a: Analysis, f: dict, now: int, cfg: Config, *,
                                      historical: bool = False):
    """20-bar level sweep/reclaim confirmed by an objective reversal candle."""
    daily = f["1d"].iloc[-1]
    h4 = f["4h"].iloc[-1]
    m5_frame = f["5m"]
    m5 = m5_frame.iloc[-1]
    previous = m5_frame.iloc[-2]
    a.asof = int(m5.close_time) + 1

    if not historical and now - a.asof > cfg.max_signal_age_seconds * 1000:
        a.status = "NO_SETUP"
        a.reasons = ["آخرین کندل برای صدور هشدار ورود بیش از حد قدیمی است"]
        return a, f

    direction = (
        1 if daily.close > daily.ema50 and daily.tenkan > daily.kijun else
        -1 if daily.close < daily.ema50 and daily.tenkan < daily.kijun else 0
    )
    if not direction:
        a.status = "NO_SETUP"
        a.reasons = ["جهت روزانه برای ستاپ پرایس‌اکشن همسو نیست"]
        a.counterevidence = ["قیمت و تنکان/کیجون روزانه جهت مشترک ندارند"]
        return a, f

    a.side = "LONG" if direction == 1 else "SHORT"
    cloud_edge = max(h4.span_a, h4.span_b) if direction == 1 else min(h4.span_a, h4.span_b)
    h4_aligned = (
        direction * (h4.close - h4.ema50) > 0
        and direction * (h4.close - cloud_edge) > 0
        and direction * (h4.tenkan - h4.kijun) > 0
    )
    if not h4_aligned:
        a.status = "NO_SETUP"
        a.reasons = ["جهت روزانه مشخص است"]
        a.counterevidence = ["تأیید روند و ابر در ۴ساعته کامل نیست"]
        return a, f

    prior = m5_frame.iloc[-21:-1]
    if len(prior) != 20:
        a.status = "BLOCKED_DATA"
        a.reasons = ["برای محاسبهٔ سطح ۲۰ کندلی تاریخچه کافی نیست"]
        return a, f

    prior_high = float(prior.high.max())
    prior_low = float(prior.low.min())
    candle_range = float(m5.high - m5.low)
    body = abs(float(m5.close - m5.open))
    upper_wick = float(m5.high - max(m5.open, m5.close))
    lower_wick = float(min(m5.open, m5.close) - m5.low)
    close_location = ((float(m5.close) - float(m5.low)) / candle_range
                      if candle_range > 0 else .5)
    if direction == 1:
        level = prior_low
        swept = float(m5.low) < level and float(m5.close) > level and float(previous.close) >= level
        sweep_depth = level - float(m5.low)
        bullish_engulfing = (
            previous.close < previous.open and m5.close > m5.open
            and m5.open <= previous.close and m5.close >= previous.open
        )
        hammer = (
            m5.close > m5.open and lower_wick >= 2 * body
            and upper_wick <= .3 * candle_range and close_location >= .65
        )
        candle_name = ("چکش صعودی" if hammer else "پوشای صعودی" if bullish_engulfing else "")
        stop = min(float(m5.low), level) - .1 * float(m5.atr14)
        directional_close = close_location >= .65
    else:
        level = prior_high
        swept = float(m5.high) > level and float(m5.close) < level and float(previous.close) <= level
        sweep_depth = float(m5.high) - level
        bearish_engulfing = (
            previous.close > previous.open and m5.close < m5.open
            and m5.open >= previous.close and m5.close <= previous.open
        )
        shooting_star = (
            m5.close < m5.open and upper_wick >= 2 * body
            and lower_wick <= .3 * candle_range and close_location <= .35
        )
        candle_name = ("ستارهٔ ثاقب نزولی" if shooting_star
                       else "پوشای نزولی" if bearish_engulfing else "")
        stop = max(float(m5.high), level) + .1 * float(m5.atr14)
        directional_close = close_location <= .35

    a.status = "PENDING"
    a.reasons = ["روند روزانه و ۴ساعته همسو است"]
    failed = []
    if not swept:
        failed.append("نفوذ به سطح ۲۰ کندلی و بسته‌شدن دوباره در سمت امن سطح رخ نداده")
    if not (0 < sweep_depth <= .5 * float(m5.atr14)):
        failed.append("عمق نفوذ از سطح بیش از نیم ATR است یا نفوذ معتبری ثبت نشده")
    if not candle_name:
        failed.append("الگوی چکش/ستارهٔ ثاقب یا پوشای معتبر تشکیل نشده")
    if not directional_close:
        failed.append("بسته‌شدن کندل در سمت انتهایی دامنه تأیید نشده")
    if failed:
        a.counterevidence = failed
        return a, f

    entry = float(m5.close)
    risk = direction * (entry - stop)
    if stop <= 0 or not cfg.min_stop_fraction <= risk / entry <= cfg.max_stop_fraction:
        a.status = "BLOCKED_RISK"
        a.counterevidence = ["فاصلهٔ حد ضرر ساختاری خارج از محدودهٔ آزمایشی است"]
        return a, f

    a.entry = entry
    a.stop = float(stop)
    a.target = entry + direction * cfg.reward_risk * risk
    a.status = "TECHNICAL_CONFIRMED"
    a.reasons.extend([
        "قیمت سطح حمایت/مقاومت ۲۰ کندلی را نفوذ کرد و دوباره پس گرفت",
        f"الگوی کندلی تأییدشده: {candle_name}",
    ])
    a.counterevidence = [
        "بازپس‌گیری سطح ممکن است شکست کاذب باشد؛ قواعد به معنی سودآوری تضمین‌شده نیستند"
    ]
    if a.entry_session == "MONITOR_ONLY":
        a.counterevidence.append("خارج از ساعت مجاز ورود؛ موقعیت باز همچنان مدیریت می‌شود")
    key = f"price-action-candlestick-v1:{cfg.fingerprint()}:{cfg.source}:{a.symbol}:{a.asof}:{a.side}"
    a.signal_id = hashlib.sha256(key.encode()).hexdigest()[:24]
    return a, f



def evaluate_breakout_retest(a: Analysis, f: dict, now: int, cfg: Config, *,
                             historical: bool = False):
    """Enter only after a high-volume 20-bar breakout is retested and reclaimed.

    The breakout must occur within the previous eight finalized bars. Entry is
    simulated by the journal at the next bar open; no future candle is inspected.
    """
    daily, h4 = f["1d"].iloc[-1], f["4h"].iloc[-1]
    bars = f["5m"]
    current = bars.iloc[-1]
    a.asof = int(current.close_time) + 1

    if not historical and now - a.asof > cfg.max_signal_age_seconds * 1000:
        a.status = "NO_SETUP"
        a.reasons = ["آخرین کندل برای صدور هشدار ورود بیش از حد قدیمی است"]
        return a, f

    direction = (
        1 if daily.close > daily.ema50 and daily.tenkan > daily.kijun else
        -1 if daily.close < daily.ema50 and daily.tenkan < daily.kijun else 0
    )
    if not direction:
        a.status = "NO_SETUP"
        a.reasons = ["جهت روزانه برای ستاپ شکست و بازآزمایی همسو نیست"]
        a.counterevidence = ["قیمت و تنکان/کیجون روزانه جهت مشترک ندارند"]
        return a, f

    a.side = "LONG" if direction == 1 else "SHORT"
    cloud_edge = max(h4.span_a, h4.span_b) if direction == 1 else min(h4.span_a, h4.span_b)
    aligned = (
        direction * (h4.close - h4.ema50) > 0
        and direction * (h4.close - cloud_edge) > 0
        and direction * (h4.tenkan - h4.kijun) > 0
    )
    if not aligned:
        a.status = "NO_SETUP"
        a.reasons = ["جهت روزانه مشخص است"]
        a.counterevidence = ["تأیید روند و ابر در ۴ساعته کامل نیست"]
        return a, f

    a.status = "PENDING"
    a.reasons = ["روند روزانه و ۴ساعته همسو است"]
    if len(bars) < 22:
        a.status = "BLOCKED_DATA"
        a.counterevidence = ["برای بررسی شکست ۲۰ کندلی و بازآزمایی سطح تاریخچه کافی نیست"]
        return a, f

    # Search only prior finalized candles, excluding the current retest candle.
    breakout = None
    first = max(20, len(bars) - 9)
    for i in range(first, len(bars) - 1):
        candle = bars.iloc[i]
        prior = bars.iloc[i - 20:i]
        level = float(prior.high.max()) if direction == 1 else float(prior.low.min())
        span = float(candle.high - candle.low)
        close_location = (float(candle.close - candle.low) / span) if span > 0 else .5
        if direction == 1:
            valid = (
                candle.close > level and candle.close > candle.open
                and candle.body_ratio >= .55 and candle.volume >= 1.2 * candle.prior_volume20
                and close_location >= .75
            )
        else:
            valid = (
                candle.close < level and candle.close < candle.open
                and candle.body_ratio >= .55 and candle.volume >= 1.2 * candle.prior_volume20
                and close_location <= .25
            )
        if valid:
            breakout = (i, level)
    if breakout is None:
        a.counterevidence = ["شکست پرحجم و معتبر سطح ۲۰ کندلی در ۸ کندل اخیر وجود ندارد"]
        return a, f

    _, level = breakout
    atr = float(current.atr14)
    span = float(current.high - current.low)
    close_location = (float(current.close - current.low) / span) if span > 0 else .5
    if direction == 1:
        retest = (
            current.low <= level + .15 * atr
            and current.low >= level - .35 * atr
            and current.close > level and current.close > current.open
            and current.body_ratio >= .35 and close_location >= .65
        )
        stop = float(min(bars.iloc[-3:].low.min(), current.low) - .1 * atr)
    else:
        retest = (
            current.high >= level - .15 * atr
            and current.high <= level + .35 * atr
            and current.close < level and current.close < current.open
            and current.body_ratio >= .35 and close_location <= .35
        )
        stop = float(max(bars.iloc[-3:].high.max(), current.high) + .1 * atr)
    if not retest:
        a.counterevidence = ["قیمت سطح شکسته‌شده را لمس نکرده یا در سمت معتبر آن بسته نشده است"]
        return a, f

    entry = float(current.close)
    risk = direction * (entry - stop)
    if stop <= 0 or not cfg.min_stop_fraction <= risk / entry <= cfg.max_stop_fraction:
        a.status = "BLOCKED_RISK"
        a.counterevidence = ["فاصلهٔ حد ضرر ساختاری خارج از محدودهٔ آزمایشی است"]
        return a, f

    a.entry = entry
    a.stop = stop
    a.target = entry + direction * cfg.reward_risk * risk
    a.status = "TECHNICAL_CONFIRMED"
    a.reasons.extend([
        "شکست ۲۰ کندلی با بدنهٔ قوی و حجم حداقل ۱٫۲ برابر میانگین تأیید شد",
        "سطح شکسته‌شده بازآزمایی شد و کندل در سمت روند بالاتر بسته شد",
    ])
    a.counterevidence = [
        "شکست و بازآزمایی ممکن است ناموفق باشد؛ این قواعد سودآوری را تضمین نمی‌کنند"
    ]
    if a.entry_session == "MONITOR_ONLY":
        a.counterevidence.append("خارج از ساعت مجاز ورود؛ موقعیت باز همچنان مدیریت می‌شود")
    key = f"breakout-retest-v1:{cfg.fingerprint()}:{cfg.source}:{a.symbol}:{a.asof}:{a.side}"
    a.signal_id = hashlib.sha256(key.encode()).hexdigest()[:24]
    return a, f


def register_additional_setups(register_setup):
    """Called once by core after its built-in evaluator has been registered."""
    register_setup("range-breakout-v1", evaluate_range_breakout)
    register_setup("price-action-candlestick-v1", evaluate_price_action_candlestick)
    register_setup("breakout-retest-v1", evaluate_breakout_retest)
