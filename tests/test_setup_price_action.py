import unittest

import pandas as pd

from watchbot.core import Analysis, Config
from watchbot.setups import evaluate_price_action_candlestick


class PriceActionCandlestickTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(symbols=("BTCUSDT",), setup_id="price-action-candlestick-v1")
        self.now = 1_800_000_000_000
        self.analysis = Analysis(
            "BTCUSDT", self.cfg.source, self.now, "PENDING", None, [], [],
            "UNKNOWN", "research only", setup_id="price-action-candlestick-v1",
        )
        daily = pd.DataFrame([{
            "close": 200.0, "ema50": 190.0, "tenkan": 195.0, "kijun": 190.0,
        }])
        h4 = pd.DataFrame([{
            "close": 150.0, "ema50": 140.0, "span_a": 145.0, "span_b": 142.0,
            "tenkan": 146.0, "kijun": 140.0,
        }])
        rows = []
        for i in range(21):
            rows.append({
                "open": 99.5, "high": 100.0, "low": 99.0, "close": 99.5,
                "atr14": 0.4, "close_time": self.now - 300_000 * (21-i),
            })
        rows.append({
            "open": 99.05, "high": 99.15, "low": 98.81, "close": 99.12,
            "atr14": 0.4, "close_time": self.now - 1,
        })
        self.frames = {"1d": daily, "4h": h4, "5m": pd.DataFrame(rows)}

    def evaluate(self):
        return evaluate_price_action_candlestick(
            self.analysis, self.frames, self.now, self.cfg, historical=True
        )[0]

    def test_confirms_support_sweep_reclaim_and_hammer(self):
        result = self.evaluate()
        self.assertEqual(result.status, "TECHNICAL_CONFIRMED")
        self.assertEqual(result.side, "LONG")
        self.assertIn("چکش صعودی", result.reasons[-1])
        self.assertLess(result.stop, result.entry)
        self.assertGreater(result.target, result.entry)

    def test_accepts_engulfing_confirmation_after_reclaim(self):
        self.frames["5m"].loc[20, ["open", "close"]] = [99.4, 99.2]
        self.frames["5m"].loc[21, ["open", "high", "low", "close"]] = [
            99.15, 99.65, 98.81, 99.6
        ]
        result = self.evaluate()
        self.assertEqual(result.status, "TECHNICAL_CONFIRMED")
        self.assertIn("پوشای صعودی", result.reasons[-1])

    def test_rejects_sweep_without_reclaim(self):
        self.frames["5m"].loc[21, ["open", "high", "low", "close"]] = [
            98.8, 99.1, 98.7, 98.95
        ]
        result = self.evaluate()
        self.assertEqual(result.status, "PENDING")
        self.assertIn("نفوذ به سطح ۲۰ کندلی و بسته‌شدن دوباره در سمت امن سطح رخ نداده",
                      result.counterevidence)

    def test_rejects_sweep_deeper_than_half_atr(self):
        self.frames["5m"].loc[21, ["open", "high", "low", "close"]] = [
            98.3, 99.15, 98.3, 99.12
        ]
        result = self.evaluate()
        self.assertEqual(result.status, "PENDING")
        self.assertIn("عمق نفوذ از سطح بیش از نیم ATR است یا نفوذ معتبری ثبت نشده",
                      result.counterevidence)


if __name__ == "__main__":
    unittest.main()
