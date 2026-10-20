from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from watchbot.core import Config, parse_time, INTERVALS, analyze
from watchbot.market import MarketCache, live_snapshot
from watchbot.engine import Engine
from watchbot.demo import fixture

NOW = parse_time("2026-10-20T12:00:01Z")


def payload(now=NOW):
    start = now // 300000 * 300000
    return {"data": {"E": now, "s": "BTCUSDT", "k": {"s": "BTCUSDT", "i": "5m",
        "t": start, "T": start + 299999, "o": "100", "h": "102", "l": "99", "c": "101", "v": "50", "x": False}}}


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(symbols=("BTCUSDT",), database=str(Path(self.tmp.name) / "j.sqlite3"),
                          output_dir=str(Path(self.tmp.name) / "charts"))

    def test_live_cache_fresh_stale_and_no_journal_creation(self):
        cache = MarketCache(self.cfg)
        self.assertTrue(cache.ingest(payload(), NOW))
        self.assertEqual(live_snapshot(self.cfg, NOW + 1000)["status"], "FRESH")
        self.assertEqual(live_snapshot(self.cfg, NOW + 16000)["status"], "STALE")
        self.assertFalse(Path(self.cfg.database).exists())

    def test_bad_geometry_wrong_symbol_and_future_event_rejected(self):
        cache = MarketCache(self.cfg)
        bad = payload();bad["data"]["k"]["h"] = "98"
        self.assertFalse(cache.ingest(bad, NOW))
        bad = payload();bad["data"]["s"] = "SOLUSDT"
        self.assertFalse(cache.ingest(bad, NOW))
        self.assertFalse(cache.ingest(payload(NOW + 60000), NOW))

    def test_out_of_order_events_cannot_replace_newer_data(self):
        cache = MarketCache(self.cfg)
        self.assertTrue(cache.ingest(payload(), NOW))
        self.assertFalse(cache.ingest(payload(NOW - 500), NOW + 500))

    def test_mismatched_experiment_and_corrupt_cache_unavailable(self):
        cache = MarketCache(self.cfg)
        cache.ingest(payload(), NOW)
        changed = replace(self.cfg, risk_fraction=.003)
        self.assertEqual(live_snapshot(changed, NOW)["status"], "UNAVAILABLE")
        (Path(self.tmp.name) / "live-market.json").write_text("invalid")
        self.assertEqual(live_snapshot(self.cfg, NOW)["status"], "UNAVAILABLE")

    def test_cached_engine_matches_uncached_and_detects_revised_data(self):
        e = Engine(self.cfg)
        self.addCleanup(e.journal.close)
        now = NOW // 300000 * 300000
        frames = {tf: fixture(now, tf) for tf in INTERVALS}
        expected, _ = analyze("BTCUSDT", frames, now, self.cfg, historical=True)
        for _ in range(2):
            actual = e.process(now, {"BTCUSDT": frames}, historical=True, render_signals=False, queue_alerts=False)[0]
            self.assertEqual(actual, expected.to_dict())
        # The same timestamp with changed values must invalidate the cached input.
        frames["4h"] = frames["4h"].copy()
        frames["4h"].loc[len(frames["4h"]) - 1, "high"] = 1
        actual = e.process(now + 1000, {"BTCUSDT": frames}, historical=True, render_signals=False, queue_alerts=False)[0]
        self.assertEqual(actual["status"], "BLOCKED_DATA")

    def test_unchanged_htf_cannot_hide_staleness_at_new_boundary(self):
        e = Engine(self.cfg)
        self.addCleanup(e.journal.close)
        now = NOW // 300000 * 300000
        frames = {tf: fixture(now, tf) for tf in INTERVALS}
        e.process(now, {"BTCUSDT": frames}, historical=True, render_signals=False, queue_alerts=False)
        result = e.process(now + 14400000, {"BTCUSDT": frames}, historical=True, render_signals=False, queue_alerts=False)
        self.assertEqual(result[0]["status"], "BLOCKED_DATA")
