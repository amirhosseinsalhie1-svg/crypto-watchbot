import unittest

import pandas as pd

from watchbot.core import Analysis, Config
from watchbot.setups import evaluate_range_breakout


class RangeBreakoutTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(symbols=("BTCUSDT",), setup_id="range-breakout-v1")
        self.now = 1_800_000_000_000
        self.analysis = Analysis(
            "BTCUSDT", self.cfg.source, self.now, "PENDING", None, [], [],
            "UNKNOWN", "research only", setup_id="range-breakout-v1",
        )
        self.daily = pd.DataFrame([{
            "close": 200.0, "ema50": 190.0, "tenkan": 195.0, "kijun": 190.0,
        }])
        self.h4 = pd.DataFrame([{
            "close": 150.0, "ema50": 140.0, "span_a": 145.0, "span_b": 142.0,
            "tenkan": 146.0, "kijun": 140.0,
        }])
        rows = []
        for i in range(21):
            rows.append({
                "open": 99.5, "high": 100.0, "low": 99.0, "close": 99.5,
                "volume": 1000.0, "atr14": 0.4, "body_ratio": 0.0,
                "prior_volume20": 1000.0, "close_time": self.now - 300_000 * (21-i),
            })
        rows.append({
            "open": 100.02, "high": 100.18, "low": 100.0, "close": 100.16,
            "volume": 1500.0, "atr14": 0.4, "body_ratio": 0.875,
            "prior_volume20": 1000.0, "close_time": self.now - 1,
        })
        self.frames = {"1d": self.daily, "4h": self.h4, "5m": pd.DataFrame(rows)}

    def evaluate(self):
        return evaluate_range_breakout(
            self.analysis, self.frames, self.now, self.cfg, historical=True
        )[0]

    def test_confirms_fresh_break_with_trend_body_and_volume(self):
        result = self.evaluate()
        self.assertEqual(result.status, "TECHNICAL_CONFIRMED")
        self.assertEqual(result.side, "LONG")
        self.assertGreater(result.target, result.entry)
        self.assertLess(result.stop, result.entry)
        self.assertTrue(result.signal_id)

    def test_rejects_repeated_breakout_after_prior_close_left_range(self):
        self.frames["5m"].loc[20, ["close", "high"]] = [100.05, 100.1]
        result = self.evaluate()
        self.assertEqual(result.status, "PENDING")
        self.assertIn("شکست تازهٔ سقف/کف ۲۰ کندل بسته‌شده تأیید نشده", result.counterevidence)

    def test_rejects_low_volume_breakout(self):
        self.frames["5m"].loc[21, "volume"] = 1100
        result = self.evaluate()
        self.assertEqual(result.status, "PENDING")
        self.assertIn("حجم کندل شکست کمتر از ۱٫۲ برابر میانگین ۲۰ کندل قبلی است",
                      result.counterevidence)

    def test_rejects_overextended_breakout(self):
        row = self.frames["5m"].index[-1]
        self.frames["5m"].loc[row, ["close", "high", "body_ratio"]] = [100.25, 100.27, .92]
        result = self.evaluate()
        self.assertEqual(result.status, "PENDING")
        self.assertIn("قیمت بیش از نیم ATR از سطح شکست امتداد یافته است",
                      result.counterevidence)


if __name__ == "__main__":
    unittest.main()
