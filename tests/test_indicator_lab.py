import tempfile
import unittest

import numpy as np
import pandas as pd

from watchbot.indicator_lab import FEATURE_COLUMNS, add_candidate_features


class IndicatorLabTests(unittest.TestCase):
    @staticmethod
    def bars(count):
        close = 100 + np.arange(count) * .02 + np.sin(np.arange(count) / 5)
        return pd.DataFrame({
            "open_time": np.arange(count, dtype=np.int64) * 300_000,
            "close_time": np.arange(count, dtype=np.int64) * 300_000 + 299_999,
            "open": close - .1,
            "high": close + .4,
            "low": close - .4,
            "close": close,
            "volume": 1000 + np.arange(count) % 17,
        })

    def test_candidate_features_are_finite_after_warmup(self):
        result = add_candidate_features(self.bars(500))
        valid = result.iloc[100:][list(FEATURE_COLUMNS)]
        self.assertTrue(np.isfinite(valid.to_numpy()).all())
        self.assertTrue(((valid.rsi14 >= 0) & (valid.rsi14 <= 100)).all())
        self.assertTrue(((valid.tsi25_13 >= -100) & (valid.tsi25_13 <= 100)).all())
        self.assertTrue((valid.tsi_hist == valid.tsi25_13 - valid.tsi_signal13).all())

    def test_future_bars_do_not_change_prior_indicator_values(self):
        original = self.bars(400)
        extended = pd.concat([original, self.bars(420).iloc[400:]], ignore_index=True)
        before = add_candidate_features(original)
        after = add_candidate_features(extended)
        pd.testing.assert_series_equal(
            before.loc[399, list(FEATURE_COLUMNS)],
            after.loc[399, list(FEATURE_COLUMNS)],
            check_names=False,
        )


if __name__ == "__main__":
    unittest.main()
