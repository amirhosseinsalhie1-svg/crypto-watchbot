import tempfile
import unittest
from pathlib import Path

from watchbot.leverage_research import build_config


class LeverageResearchTests(unittest.TestCase):
    def test_sweep_changes_only_margin_leverage_and_isolates_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = build_config("config.portfolio-research.json", temp, 15)
        self.assertEqual(cfg.paper_leverage, 15)
        self.assertEqual(len(cfg.symbols), 10)
        self.assertEqual(cfg.max_open_positions, 2)
        self.assertEqual(cfg.min_net_reward_risk, 3)
        self.assertEqual(cfg.risk_fraction, 0.0025)
        self.assertEqual(cfg.max_paper_notional_multiple, 1)
        self.assertTrue(cfg.database.endswith("journal.sqlite3"))

    def test_rejects_leverage_outside_user_range(self):
        for value in (9, 21, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_config("config.portfolio-research.json", "runtime/test", value)


if __name__ == "__main__":
    unittest.main()
