from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import pandas as pd

from watchbot.core import Config, INTERVALS, parse_time
from watchbot.demo import fixture
from watchbot.engine import Engine, single_instance
from watchbot.io import BinanceReference, NetworkError, Telegram, request_json

NOW = parse_time("2026-10-20T12:00:00Z")


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Config(symbols=("SOLUSDT",), database=str(self.root / "test.sqlite3"),
                          output_dir=str(self.root / "charts"), news_file=str(self.root / "news.json"))

    def test_render_journal_and_duplicate_scan(self):
        engine = Engine(self.cfg)
        self.addCleanup(engine.journal.close)
        data = {"SOLUSDT": {tf: fixture(NOW, tf) for tf in INTERVALS}}
        reports = engine.process(NOW, data)
        self.assertEqual(reports[0]["status"], "TECHNICAL_CONFIRMED")
        photo = next((self.root / "charts").glob("*.png"))
        self.assertTrue(photo.read_bytes().startswith(b"\x89PNG"))
        self.assertIn("خبر", photo.with_suffix(".txt").read_text())
        engine.process(NOW + 1000, data)
        self.assertEqual(engine.journal.db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 1)
        self.assertEqual(engine.journal.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)
        self.assertFalse(engine.journal.summary()["pending"])

    def test_provider_closed_rows_and_cache_with_mock_server(self):
        provider = BinanceReference(self.cfg)
        calls = []
        def fake(endpoint, **params):
            calls.append(endpoint)
            if endpoint.endswith("time"):
                return {"serverTime": NOW}
            frame = fixture(NOW, params["interval"])
            return [[int(r.open_time), str(r.open), str(r.high), str(r.low), str(r.close),
                     str(r.volume), int(r.close_time)] for r in frame.itertuples()]
        with patch.object(provider, "get", side_effect=fake), patch("watchbot.io.utc_ms", return_value=NOW):
            at, data = provider.snapshot(["SOLUSDT"])
            self.assertEqual(at, NOW)
            self.assertEqual(len(data["SOLUSDT"]["5m"]), 300)
            provider.snapshot(["SOLUSDT"])
        self.assertEqual(calls.count("/fapi/v1/klines"), 3)

    def test_clock_skew_stops_provider(self):
        provider = BinanceReference(self.cfg)
        with patch.object(provider, "get", return_value={"serverTime": NOW}), patch("watchbot.io.utc_ms", return_value=NOW + 60_000):
            with self.assertRaises(NetworkError):
                provider.snapshot(["SOLUSDT"])

    def test_telegram_caption_upload_and_token_not_logged(self):
        img = self.root / "chart.png"
        img.write_bytes(b"fake image for transport unit test")
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "secret-test-token", "TELEGRAM_CHAT_ID": "123"}):
            tg = Telegram()
        with patch("watchbot.io.request_json", return_value={"ok": True, "result": {"message_id": 1}}) as call:
            tg.photo(str(img), "تحلیل آزمایشی")
            self.assertIn(b"image/png", call.call_args.kwargs["data"])
            self.assertIn("تحلیل آزمایشی".encode(), call.call_args.kwargs["data"])
        error = urllib.error.HTTPError("https://api.telegram.org/botSECRET", 401, "unauthorized", {}, None)
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(NetworkError) as caught:
                request_json("https://api.telegram.org/botSECRET", attempts=1)
        self.assertNotIn("SECRET", str(caught.exception))

    def test_paused_outbox_does_not_send(self):
        engine = Engine(self.cfg)
        self.addCleanup(engine.journal.close)
        engine.journal.put("paused", True)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "123"}):
            tg = Telegram()
        with patch.object(tg, "photo") as send:
            tg.flush(engine.journal)
            send.assert_not_called()

    def test_single_writer_lock(self):
        with single_instance(self.cfg.database):
            with self.assertRaises(RuntimeError):
                with single_instance(self.cfg.database):
                    pass

    def test_walk_forward_ignores_future_reversal(self):
        engine = Engine(self.cfg)
        self.addCleanup(engine.journal.close)
        f = {tf: fixture(NOW, tf) for tf in INTERVALS}
        future = fixture(NOW + 300_000, "5m").iloc[[-1]].copy()
        future.loc[:, ["open", "high", "low", "close"]] = [100, 101, 1, 2]
        f["5m"] = pd.concat([f["5m"], future], ignore_index=True)
        reports = engine.process(NOW, {"SOLUSDT": f}, historical=True, queue_alerts=False,
                                 render_signals=False)
        self.assertEqual(reports[0]["status"], "TECHNICAL_CONFIRMED")
        self.assertEqual(engine.journal.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
