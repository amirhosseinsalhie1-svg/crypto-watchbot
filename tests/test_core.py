from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from watchbot.core import Config, INTERVALS, analyze, indicators, news_gate, parse_time, validate_frame, iso
from watchbot.demo import fixture

NOW = parse_time("2026-10-20T12:00:00+00:00")


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "news.json")
        self.cfg = Config(news_file=self.path)

    def frames(self, short=False):
        return {tf: fixture(NOW, tf, short=short) for tf in INTERVALS}

    def test_confirmed_long_and_short_are_symmetric(self):
        for short, side in [(False, "LONG"), (True, "SHORT")]:
            a, _ = analyze("SOLUSDT", self.frames(short), NOW, self.cfg)
            self.assertEqual(a.status, "TECHNICAL_CONFIRMED")
            self.assertEqual(a.side, side)
            direction = -1 if short else 1
            self.assertGreater(direction * (a.entry - a.stop), 0)
            self.assertAlmostEqual(direction * (a.target - a.entry), 2 * direction * (a.entry - a.stop))

    def test_unknown_news_does_not_masquerade_as_clear(self):
        self.assertEqual(news_gate(self.path, "SOLUSDT", NOW)[0], "UNKNOWN")

    def write_news(self, **kw):
        data = {"reviewed_at": iso(NOW - 60_000), "valid_until": iso(NOW + 60_000),
                "sources": ["https://www.federalreserve.gov/"], "events": []}
        data.update(kw)
        Path(self.path).write_text(json.dumps(data))

    def test_expiring_news_and_events(self):
        self.write_news()
        self.assertEqual(news_gate(self.path, "SOLUSDT", NOW)[0], "CLEAR_REVIEWED")
        self.assertEqual(news_gate(self.path, "SOLUSDT", NOW + 120_000)[0], "UNKNOWN")
        self.write_news(events=[{"start": iso(NOW - 1000), "end": iso(NOW + 1000),
                                 "name": "CPI", "symbols": ["SOLUSDT"]}])
        self.assertEqual(news_gate(self.path, "SOLUSDT", NOW)[0], "BLOCKED")
        self.assertEqual(news_gate(self.path, "BTCUSDT", NOW)[0], "CLEAR_REVIEWED")

    def test_future_review_is_unknown(self):
        self.write_news(reviewed_at=iso(NOW + 1000))
        self.assertEqual(news_gate(self.path, "SOLUSDT", NOW)[0], "UNKNOWN")

    def test_unfinished_extreme_bar_cannot_change_signal(self):
        f = self.frames()
        a, _ = analyze("SOLUSDT", f, NOW, self.cfg)
        extra = fixture(NOW + 300_000, "5m").iloc[[-1]].copy()
        extra.loc[:, "high"] = 999999
        import pandas as pd
        f["5m"] = pd.concat([f["5m"], extra], ignore_index=True)
        b, _ = analyze("SOLUSDT", f, NOW, self.cfg)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_cloud_uses_past_values(self):
        d = fixture(NOW, "4h")
        i = indicators(d)
        self.assertAlmostEqual(i.span_a.iloc[-1], (i.tenkan.iloc[-27] + i.kijun.iloc[-27]) / 2)
        extreme = d.copy()
        extreme.loc[len(d) - 1, "high"] = 1_000_000
        self.assertEqual(indicators(extreme).span_a.iloc[-1], i.span_a.iloc[-1])
        self.assertEqual(indicators(extreme).span_b.iloc[-1], i.span_b.iloc[-1])

    def test_stale_signal_blocked(self):
        a, _ = analyze("SOLUSDT", self.frames(), NOW + 60_000, self.cfg)
        self.assertEqual(a.status, "BLOCKED_DATA")

    def test_data_corruption_gap_duplicate_and_nan_rejected(self):
        d = fixture(NOW, "5m")
        variants = [d.drop(index=100), d.iloc[::-1], d.assign(close=np.nan),
                    d.assign(high=d.low - 1), d.assign(close_time=d.close_time + 1)]
        import pandas as pd
        variants.append(pd.concat([d, d.iloc[[-1]]]))
        for broken in variants:
            with self.subTest(kind=str(broken.shape)):
                with self.assertRaises(ValueError):
                    validate_frame(broken, "5m", NOW)

    def test_daily_mixed_prevents_setup(self):
        f = self.frames()
        f["1d"].loc[:, ["open", "high", "low", "close"]] = [100, 101, 99, 100]
        a, _ = analyze("SOLUSDT", f, NOW, self.cfg)
        self.assertEqual(a.status, "NO_SETUP")
        self.assertIsNone(a.entry)

    def test_missing_timeframe_blocks(self):
        a, _ = analyze("SOLUSDT", {}, NOW, self.cfg)
        self.assertEqual(a.status, "BLOCKED_DATA")

    def test_bad_config_rejected(self):
        for options in [dict(risk_fraction=.5), dict(equity=-1), dict(paper_enabled="false"),
                        dict(history_bars=2), dict(fee_bps_per_side=-1),
                        dict(reward_risk=float("nan")), dict(max_hold_bars=2.5)]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    Config(**options)

    def test_timestamp_requires_timezone(self):
        with self.assertRaises(ValueError):
            parse_time("2026-10-20T12:00:00")


if __name__ == "__main__":
    unittest.main()
