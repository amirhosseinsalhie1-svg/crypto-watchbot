from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from watchbot.core import Analysis, Config, INTERVALS, parse_time
from watchbot.journal import Journal, trading_day

NOW = parse_time("2026-10-20T12:00:00Z")


def bar(at, op=100, high=100.5, low=99.5, close=100):
    return pd.DataFrame([dict(open_time=at, open=op, high=high, low=low, close=close,
                              volume=1000, close_time=at + 299_999)])


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(database=str(Path(self.tmp.name) / "j.sqlite3"), paper_enabled=True)
        self.j = Journal(self.cfg)
        self.addCleanup(lambda: self.j.close() if self.j else None)
        self.a = Analysis("SOLUSDT", self.cfg.source, NOW, "TECHNICAL_CONFIRMED", "LONG",
                          [], [], "CLEAR_REVIEWED", "reviewed", 100, 99, 102, "signal-one")

    def seed(self):
        with self.j.transaction():
            self.j.advance("SOLUSDT", bar(NOW - 300_000))
            self.j.schedule(self.a)

    def step(self, d):
        with self.j.transaction():
            self.j.advance("SOLUSDT", d, ignore_news=True)

    def test_no_fill_on_signal_candle_and_fill_on_next_open(self):
        self.seed()
        self.assertEqual(self.j.summary()["open_positions"], 0)
        self.step(bar(NOW, op=100.1, high=100.5))
        p = self.j.db.execute("SELECT * FROM trades").fetchone()
        self.assertEqual(p["entry_at"], NOW)
        self.assertGreater(p["entry"], 100.1)  # adverse entry slippage

    def test_both_levels_touched_stop_wins_costs_and_risk_budget(self):
        self.seed()
        self.step(bar(NOW, high=105, low=95))
        p = self.j.db.execute("SELECT * FROM trades").fetchone()
        self.assertEqual(p["reason"], "STOP_AMBIGUOUS")
        self.assertLess(p["pnl"], 0)
        self.assertLessEqual(-p["pnl"], self.cfg.equity * self.cfg.risk_fraction + 1e-8)
        self.assertLess(p["exit"], 99)

    def test_gap_through_stop_can_exceed_planned_loss(self):
        self.seed()
        self.step(bar(NOW))
        self.step(bar(NOW + 300_000, op=95, high=96, low=94, close=95))
        p = self.j.db.execute("SELECT * FROM trades").fetchone()
        self.assertLess(p["exit"], 95)
        self.assertGreater(-p["pnl"], self.cfg.equity * self.cfg.risk_fraction)

    def test_oversized_entry_gap_cancels(self):
        self.seed()
        self.step(bar(NOW, op=101, high=102, low=100.5, close=101))
        self.assertEqual(self.j.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0], 0)
        self.assertIsNone(self.j.get("pending"))

    def test_paper_news_unknown_blocks_entry(self):
        self.seed()
        with self.j.transaction():
            self.j.advance("SOLUSDT", bar(NOW))
        self.assertEqual(self.j.summary()["open_positions"], 0)

    def test_restart_does_not_duplicate_fill(self):
        self.seed()
        self.step(bar(NOW, high=105, low=95))
        before = self.j.summary()
        self.j.close()
        self.j = Journal(self.cfg)
        self.step(bar(NOW, high=105, low=95))
        self.assertEqual(self.j.summary(), before)

    def test_only_one_position_and_pending_globally(self):
        self.seed()
        with self.j.transaction():
            self.j.schedule(replace(self.a, symbol="BTCUSDT", signal_id="two"))
        self.assertEqual(self.j.get("pending")["symbol"], "SOLUSDT")

    def test_daily_loss_and_two_losses_stop(self):
        with self.j.transaction():
            self.j.day(NOW)
            self.j.put("consecutive_losses", 2)
            self.assertFalse(self.j.eligible(NOW))
            self.j.put("consecutive_losses", 0)
            self.j.put("daily_gross_losses", 10)
            self.assertFalse(self.j.eligible(NOW))

    def test_day_reset_uses_tehran_midnight(self):
        self.assertEqual(trading_day(parse_time("2026-10-20T20:29:00Z")), "2026-10-20")
        self.assertEqual(trading_day(parse_time("2026-10-20T20:30:00Z")), "2026-10-21")

    def test_transaction_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.j.transaction():
                self.j.put("pending", {"fake": True})
                raise RuntimeError("crash")
        self.assertIsNone(self.j.get("pending"))

    def test_changed_config_cannot_mix_experiments(self):
        with self.assertRaises(ValueError):
            Journal(replace(self.cfg, fee_bps_per_side=8))

    def test_position_stays_open_after_planning_horizon_until_target(self):
        self.seed()
        self.step(bar(NOW))
        with self.j.transaction():
            self.j.db.execute("UPDATE trades SET bars=?", (self.cfg.max_hold_bars - 1,))
        self.step(bar(NOW + 300_000))
        trade = self.j.db.execute("SELECT * FROM trades").fetchone()
        self.assertIsNone(trade["exit_at"])
        self.assertEqual(self.j.summary()["open_positions"], 1)
        self.step(bar(NOW + 600_000, high=103, low=99.5, close=102.5))
        trade = self.j.db.execute("SELECT * FROM trades").fetchone()
        self.assertEqual(trade["reason"], "TARGET")
        self.assertEqual(self.j.summary()["open_positions"], 0)

    def test_history_gap_halts(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.step(bar(NOW + 600_000))

    def test_outbox_deduplicated(self):
        with self.j.transaction():
            self.j.enqueue(self.a, "caption", "chart.png")
            self.j.enqueue(self.a, "caption", "chart.png")
        self.assertEqual(self.j.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
