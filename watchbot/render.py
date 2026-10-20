from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np

from .core import Analysis, iso


def caption(a: Analysis) -> str:
    labels = {"TECHNICAL_CONFIRMED": "تأیید فنی آزمایشی", "PENDING": "در انتظار تأیید",
              "NO_SETUP": "بدون ستاپ", "BLOCKED_DATA": "توقف به علت داده",
              "BLOCKED_RISK": "توقف به علت ریسک"}
    tehran = datetime.fromtimestamp(a.asof / 1000, timezone.utc).astimezone(ZoneInfo("Asia/Tehran"))
    lines = [f"{a.symbol} | {labels[a.status]} | {a.side or '—'}",
             "فقط پژوهش و معاملهٔ فرضی", f"منبع: {a.source}",
             f"زمان تهران: {tehran:%Y-%m-%d %H:%M}",
             f"خبر: {a.news_status} — {a.news_note}"]
    if a.entry is not None:
        lines += [f"مرجع سناریو: {a.entry:.8g}", f"ابطال: {a.stop:.8g}",
                  f"هدف فرضی: {a.target:.8g}", "انقضای هشدار: یک کندل ۵دقیقه"]
    lines += ["دلایل: " + "؛ ".join(a.reasons), "ملاحظات: " + "؛ ".join(a.counterevidence),
              f"نسخه: {a.strategy} | {a.config_hash}"]
    return "\n".join(lines)[:1000]


def chart(a: Analysis, frames: dict, directory: str) -> str:
    """Plot exact OHLC data; never generate market geometry with an image model."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = Path(directory) / f"{a.symbol}-{a.asof}.png"
    with plt.rc_context({"figure.facecolor": "#101827", "axes.facecolor": "#101827",
                         "axes.edgecolor": "#667085", "text.color": "#ecf0f5",
                         "axes.labelcolor": "#ecf0f5", "xtick.color": "#aab5c4",
                         "ytick.color": "#aab5c4", "font.size": 9}):
        fig, axes = plt.subplots(3, 1, figsize=(14, 12), constrained_layout=True)
        for ax, tf in zip(axes, ["1d", "4h", "5m"]):
            d = frames[tf].tail(90).reset_index(drop=True)
            x = np.arange(len(d))
            for i, r in d.iterrows():
                color = "#35ce9a" if r.close >= r.open else "#f76b83"
                ax.vlines(i, r.low, r.high, color=color, linewidth=.8)
                height = max(abs(r.close - r.open), r.close * .00001)
                ax.add_patch(Rectangle((i - .3, min(r.open, r.close)), .6, height, color=color))
            ax.plot(x, d.ema50, color="#ffbc5c", label="EMA50", linewidth=1.2)
            ax.plot(x, d.tenkan, color="#67c6ff", label="Tenkan 9", linewidth=.8)
            ax.plot(x, d.kijun, color="#c7a5ff", label="Kijun 26", linewidth=.8)
            sa, sb = d.span_a.to_numpy(float), d.span_b.to_numpy(float)
            ax.fill_between(x, sa, sb, where=sa >= sb, color="#35ce9a", alpha=.13)
            ax.fill_between(x, sa, sb, where=sa < sb, color="#f76b83", alpha=.13)
            if tf == "5m" and a.entry is not None:
                for value, name, color in [(a.entry, "Reference", "#ffffff"),
                                            (a.stop, "Invalidation", "#ff6b86"),
                                            (a.target, "Paper target", "#35ce9a")]:
                    ax.axhline(value, color=color, linestyle="--", linewidth=1,
                               label=f"{name} {value:.6g}")
            ticks = np.linspace(0, len(d) - 1, 6, dtype=int)
            labels = [datetime.fromtimestamp(int(d.iloc[i].open_time) / 1000, timezone.utc).strftime(
                "%m-%d\n%H:%M") for i in ticks]
            ax.set_xticks(ticks, labels)
            ax.set_title(f"{tf} | Finalized UTC candles", loc="left", fontsize=11)
            ax.grid(alpha=.10)
            ax.legend(loc="upper left", fontsize=8, facecolor="#101827", labelcolor="#ecf0f5")
            ax.set_xlim(-1, len(d) + 1)
        fig.suptitle(f"{a.symbol} | {a.status} | {a.side or 'WAIT'}\n"
                     f"{a.source} | {iso(a.asof)} | PAPER / RESEARCH ONLY\n"
                     f"News: {a.news_status} | {a.strategy}", fontsize=13)
        fig.savefig(path, dpi=130)
        plt.close(fig)
    return str(path.resolve())
