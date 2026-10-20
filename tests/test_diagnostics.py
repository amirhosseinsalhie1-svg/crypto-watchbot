from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from watchbot.cli import main
from watchbot.core import Config, INTERVALS, parse_time
from watchbot.demo import fixture
from watchbot.diagnostics import doctor, validate_snapshot
from watchbot.engine import run_live
from watchbot.io import NetworkError

NOW = parse_time("2026-10-20T12:00:00Z")


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Config(symbols=("BTCUSDT",), database=str(self.root / "journal.sqlite3"),
                          output_dir=str(self.root / "charts"), news_file=str(self.root / "news.json"))
        env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
                                      "TELEGRAM_OPERATOR_USER_ID": ""})
        env.start()
        self.addCleanup(env.stop)
        self.snapshots = {"BTCUSDT": {tf: fixture(NOW, tf) for tf in INTERVALS}}

    def test_offline_doctor_never_uses_network_or_creates_journal(self):
        with patch("watchbot.io.request_json") as network:
            report = doctor(self.cfg)
        network.assert_not_called()
        self.assertTrue(report["ok"])
        self.assertFalse(report["market_verified"])
        self.assertFalse(report["telegram_delivery_verified"])
        self.assertFalse(Path(self.cfg.database).exists())

    def test_partial_credentials_fail_without_exposing_token(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "PRIVATE_TOKEN"}):
            report = doctor(self.cfg)
        self.assertFalse(report["ok"])
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(report))

    def test_live_doctor_checks_all_frames_without_telegram_calls(self):
        with patch("watchbot.diagnostics.BinanceReference") as provider, \
                patch("watchbot.io.Telegram.call") as telegram:
            provider.return_value.available_symbols.return_value = {"BTCUSDT"}
            provider.return_value.snapshot.return_value = NOW, self.snapshots
            report = doctor(self.cfg, live=True)
        telegram.assert_not_called()
        self.assertTrue(report["market_verified"])
        self.assertEqual(report["market"]["BTCUSDT"]["4h"]["closed_bars"], 300)

    def test_live_failure_is_redacted_and_not_marked_verified(self):
        with patch("watchbot.diagnostics.BinanceReference") as provider:
            provider.return_value.available_symbols.side_effect = NetworkError("secret URL")
            report = doctor(self.cfg, live=True)
        self.assertFalse(report["ok"])
        self.assertFalse(report["market_verified"])
        self.assertNotIn("secret URL", json.dumps(report))

    def test_snapshot_rejects_missing_configured_symbol(self):
        cfg = replace(self.cfg, symbols=("BTCUSDT", "SOLUSDT"))
        with self.assertRaisesRegex(ValueError, "SOLUSDT/5m"):
            validate_snapshot(cfg, NOW, self.snapshots)

    def test_snapshot_rejects_boundary_crossed_during_fetch(self):
        with self.assertRaisesRegex(ValueError, "BTCUSDT/5m"):
            validate_snapshot(self.cfg, NOW + INTERVALS["5m"], self.snapshots)

    def test_rejected_live_scan_does_not_write_heartbeat_or_analyses(self):
        with patch("watchbot.engine.BinanceReference") as provider, redirect_stdout(StringIO()):
            provider.return_value.available_symbols.return_value = {"BTCUSDT"}
            provider.return_value.snapshot.return_value = NOW + INTERVALS["5m"], self.snapshots
            with self.assertRaisesRegex(ValueError, "Incomplete market snapshot"):
                asyncio.run(run_live(self.cfg, once=True))
        self.assertFalse((self.root / "heartbeat.json").exists())
        import sqlite3
        with sqlite3.connect(self.cfg.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 0)

    def test_doctor_cli_returns_failure_code_and_json(self):
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps({"database": self.cfg.database}), encoding="utf8")
        output = StringIO()
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123"}), redirect_stdout(output):
            result = main(["--config", str(config_path), "doctor"])
        self.assertEqual(result, 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertFalse(Path(self.cfg.database).exists())
