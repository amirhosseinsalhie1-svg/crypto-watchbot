from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import pandas as pd

from watchbot.archive import read_archive
from watchbot.backtest import marked_equity, run_backtest
from watchbot.core import Config, Analysis, entry_window_open, parse_time, analyze, INTERVALS
from watchbot.demo import fixture
from watchbot.journal import Journal

NOW = parse_time("2026-09-14T12:00:00Z")


def bar(at, op=100, high=100.5, low=99.5, close=100):
    return pd.DataFrame([dict(open_time=at, open=op, high=high, low=low, close=close,
                              volume=1000, close_time=at + 299999)])


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Config(symbols=("SOLUSDT",), database=str(self.root / "j.sqlite3"),
            paper_enabled=True, fee_bps_per_side=0, slippage_bps_per_side=0,
            funding_bps_per_8h=0, reward_risk=3, tp1_close_fraction=.5)

    def journal(self, cfg=None):
        j = Journal(cfg or self.cfg)
        self.addCleanup(j.close)
        return j

    def enter(self, j):
        a = Analysis("SOLUSDT", self.cfg.source, NOW, "TECHNICAL_CONFIRMED", "LONG",
                     [], [], "UNKNOWN", "unknown", 100, 99, 103, "s1")
        with j.transaction():
            j.advance("SOLUSDT", bar(NOW - 300000))
            j.schedule(a, ignore_news=True)
            j.advance("SOLUSDT", bar(NOW), ignore_news=True)

    def test_session_boundaries_and_overnight(self):
        c = replace(self.cfg, entry_windows=("16:00-20:00",))
        for stamp, expected in [("2026-09-14T15:59:59+03:30", False),
                                ("2026-09-14T16:00:00+03:30", True),
                                ("2026-09-14T20:00:00+03:30", False)]:
            self.assertEqual(entry_window_open(c, parse_time(stamp)), expected)
        c = replace(c, entry_windows=("23:00-02:00",))
        self.assertTrue(entry_window_open(c, parse_time("2026-09-14T01:00:00+03:30")))
        self.assertFalse(entry_window_open(c, parse_time("2026-09-14T12:00:00+03:30")))

    def test_bad_windows_and_partial_targets_rejected(self):
        for options in [dict(entry_windows=("25:00-12:00",)), dict(entry_windows=("12:00-12:00",)),
                        dict(tp1_close_fraction=1), dict(tp1_close_fraction=.5, tp1_reward_risk=3),
                        dict(session_timezone="Mars/Unknown")]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                replace(self.cfg, **options)

    def test_extension_defaults_preserve_existing_config_hash(self):
        c = Config()
        old = asdict(c)
        for key in ["strategy_profile", "entry_windows", "session_timezone", "tp1_close_fraction", "tp1_reward_risk"]:
            old.pop(key)
        self.assertEqual(c.fingerprint(), hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()[:16])

    def test_session_blocks_new_entry_but_not_stop_management(self):
        c = replace(self.cfg, entry_windows=("15:00-16:00",))  # NOW is 15:30 Tehran
        j = self.journal(c)
        self.enter(j)
        with j.transaction():
            # Direct bar avoids artificial missing-data fill; gate itself never blocks exits.
            j._mark(j.db.execute("SELECT * FROM trades").fetchone(), bar(NOW + 3600000, op=98, high=98.5, low=97, close=98).iloc[0])
        self.assertEqual(j.summary()["open_positions"], 0)
        a = Analysis("SOLUSDT", c.source, NOW + 3600000, "TECHNICAL_CONFIRMED", "LONG", [], [],
                     "CLEAR_REVIEWED", "", 100, 99, 103, "s2")
        with j.transaction():
            j.schedule(a, ignore_news=True)
        self.assertIsNone(j.get("pending"))

    def test_tp1_then_same_bar_breakeven_is_conservative(self):
        j = self.journal()
        self.enter(j)
        with j.transaction():
            j.advance("SOLUSDT", bar(NOW + 300000, high=102, low=99.5, close=100.5), ignore_news=True)
        trade = j.db.execute("SELECT * FROM trades").fetchone()
        self.assertEqual(trade["reason"], "BE_AMBIGUOUS_AFTER_TP1")
        self.assertAlmostEqual(trade["pnl"], trade["qty"] * .75)
        self.assertEqual(j.db.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[0], 2)

    def test_partial_then_target_books_cash_once_and_has_exact_costs(self):
        c = replace(self.cfg, fee_bps_per_side=5, funding_bps_per_8h=3)
        j = self.journal(c)
        self.enter(j)
        with j.transaction():
            j.advance("SOLUSDT", bar(NOW + 300000, op=101, high=102, low=100.5, close=101.8), ignore_news=True)
        self.assertGreater(j.get("equity"), c.equity)
        self.assertEqual(j.summary()["open_positions"], 1)
        with j.transaction():
            j.advance("SOLUSDT", bar(NOW + 600000, op=102, high=104, low=101.5, close=103), ignore_news=True)
        fills = j.db.execute("SELECT * FROM trade_fills").fetchall()
        p = j.db.execute("SELECT * FROM trades").fetchone()
        self.assertAlmostEqual(sum(f["qty"] for f in fills), p["qty"])
        self.assertAlmostEqual(j.get("equity") - c.equity, p["pnl"])
        self.assertAlmostEqual(sum(f["pnl"] for f in fills), p["pnl"])
        expected_fees = .5 * p["qty"] * ((100 + 101.5) + (100 + 103)) * .0005
        self.assertAlmostEqual(j.summary()["fees_usdt"], expected_fees)

    def test_first_loss_drawdown_and_gap_fill(self):
        j = self.journal()
        self.enter(j)
        with j.transaction():
            j.advance("SOLUSDT", bar(NOW + 300000, op=90, high=91, low=89, close=90), ignore_news=True)
        p = j.db.execute("SELECT * FROM trades").fetchone()
        self.assertEqual(p["exit"], 90)
        self.assertGreater(j.summary()["max_closed_equity_drawdown"], 0)

    def test_open_trade_is_marked_not_discarded(self):
        j = self.journal()
        self.enter(j)
        self.assertEqual(j.summary()["closed_trades"], 0)
        self.assertEqual(j.summary()["open_positions"], 1)
        self.assertLess(marked_equity(j, {"SOLUSDT": 99.5}, NOW + 300000), self.cfg.equity)

    def test_missing_news_still_blocks_paper_entry(self):
        j = self.journal()
        a = Analysis("SOLUSDT", self.cfg.source, NOW, "TECHNICAL_CONFIRMED", "LONG", [], [],
                     "UNKNOWN", "", 100, 99, 103, "unknown")
        with j.transaction():
            j.schedule(a)
        self.assertIsNone(j.get("pending"))

    def test_guarded_prefix_and_warmup(self):
        cfg = replace(self.cfg, strategy_profile="guarded-v1")
        f = {tf: fixture(NOW, tf) for tf in INTERVALS}
        a, _ = analyze("SOLUSDT", f, NOW, cfg, historical=True)
        for tf, d in list(f.items()):
            next_bar = d.iloc[[-1]].copy()
            next_bar.loc[:, "open_time"] += INTERVALS[tf]
            next_bar.loc[:, "close_time"] += INTERVALS[tf]
            next_bar.loc[:, "high"] *= 100
            f[tf] = pd.concat([d, next_bar], ignore_index=True)
        b, _ = analyze("SOLUSDT", f, NOW, cfg, historical=True)
        self.assertEqual(a.to_dict(), b.to_dict())
        f = {tf: d.iloc[:249] for tf, d in f.items()}
        c, _ = analyze("SOLUSDT", f, NOW, cfg, historical=True)
        self.assertEqual(c.status, "BLOCKED_DATA")

    def test_archive_csv_with_and_without_header(self):
        name = "SOLUSDT-5m-2026-09.zip"
        row = "0,100,101,99,100,20,299999,0,0,0,0,0\n"
        for header in ["", "open_time,open,high,low,close,volume,close_time,a,b,c,d,e\n"]:
            out = io.BytesIO()
            with ZipFile(out, "w") as z:
                z.writestr(name.replace("zip", "csv"), header + row)
            data = read_archive(out.getvalue(), name)
            self.assertEqual(len(data), 1)
            self.assertEqual(data.iloc[0].close_time, 299999)

    def test_backtest_refuses_altered_dataset_before_creating_journal(self):
        (self.root / "manifest.json").write_text(json.dumps({"source": self.cfg.source,
                "files": [{"name": "SOLUSDT_5m.csv", "sha256": "invalid"}]}))
        (self.root / "SOLUSDT_5m.csv").write_text("altered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            run_backtest(self.cfg, str(self.root), NOW, NOW + 300000, str(self.root / "out"))
        self.assertFalse(Path(self.cfg.database).exists())

    def test_bounded_replay_uses_next_bar_and_reports_first_loss(self):
        # Deliberately synthetic inputs test behavior, not financial performance.
        data = self.root / "data"
        data.mkdir()
        files = []
        for tf in INTERVALS:
            frame = fixture(NOW, tf)
            if tf == "5m":
                frame = pd.concat([frame, bar(NOW, op=100, high=100.1, low=90, close=95)], ignore_index=True)
            path = data / f"SOLUSDT_{tf}.csv"
            frame.to_csv(path, index=False)
            files.append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        (data / "manifest.json").write_text(json.dumps({"source": self.cfg.source, "files": files}))
        report = run_backtest(self.cfg, str(data), NOW, NOW + 300000, str(self.root / "out"), ignore_news=True)
        self.assertEqual(report["summary"]["closed_trades"], 1)
        self.assertLess(report["marked_net_return_pct"], 0)
        self.assertGreater(report["max_5m_close_marked_drawdown_pct"], 0)
        trades = pd.read_csv(self.root / "out" / "ledger" / "trades.csv")
        self.assertEqual(trades.iloc[0].entry_at, NOW)
        self.assertEqual(report["news_policy"], "UNSCREENED_RESEARCH")


if __name__ == "__main__":
    unittest.main()
