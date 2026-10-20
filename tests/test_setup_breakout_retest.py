import unittest

import pandas as pd

from watchbot.core import Analysis, Config
from watchbot.setups import evaluate_breakout_retest


class BreakoutRetestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(symbols=("BTCUSDT",), setup_id="breakout-retest-v1")
        self.now = 1_800_000_000_000
        self.analysis = Analysis(
            "BTCUSDT", self.cfg.source, self.now, "PENDING", None, [], [],
            "UNKNOWN", "research only", setup_id="breakout-retest-v1",
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
                "volume": 1000.0, "atr14": .4, "body_ratio": 0.0,
                "prior_volume20": 1000.0, "close_time": self.now - 300_000 * (22-i),
            })
        rows.append({
            "open": 99.9, "high": 100.5, "low": 99.8, "close": 100.4,
            "volume": 1500.0, "atr14": .4, "body_ratio": .714,
            "prior_volume20": 1000.0, "close_time": self.now - 300_000,
        })
        rows.append({
            "open": 100.18, "high": 100.42, "low": 100.03, "close": 100.36,
            "volume": 900.0, "atr14": .4, "body_ratio": .462,
            "prior_volume20": 1000.0, "close_time": self.now - 1,
        })
        self.frames = {"1d": daily, "4h": h4, "5m": pd.DataFrame(rows)}

    def evaluate(self):
        return evaluate_breakout_retest(
            self.analysis, self.frames, self.now, self.cfg, historical=True
        )[0]

    def test_confirms_volume_breakout_followed_by_retest_reclaim(self):
        result = self.evaluate()
        self.assertEqual(result.status, "TECHNICAL_CONFIRMED")
        self.assertEqual(result.side, "LONG")
        self.assertLess(result.stop, result.entry)
        self.assertGreater(result.target, result.entry)
        self.assertTrue(result.signal_id)

    def test_rejects_breakout_without_retest(self):
        self.frames["5m"].loc[22, ["low", "close", "high"]] = [100.3, 100.5, 100.6]
        result = self.evaluate()
        self.assertEqual(result.status, "PENDING")
        self.assertIn("قیمت سطح شکسته‌شده را لمس نکرده", result.counterevidence)

    def test_rejects_unconfirmed_breakout_volume(self):
        self.frames["5m"].loc[21, "volume"] = 1100
        result = self.evaluate()
        self.assertEqual(result.status, "PENDING")
        self.assertIn("شکست پرحجم و معتبر سطح ۲۰ کندلی", result.counterevidence)


if __name__ == "__main__":
    unittest.main()
