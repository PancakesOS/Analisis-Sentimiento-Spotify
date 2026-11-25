"""Pruebas de las variables del modelo predictivo y de su reproducibilidad."""

import unittest

import numpy as np
import pandas as pd

from delivery.model.train import FEATURES, build_features, fingerprint


def dataset() -> pd.DataFrame:
    return pd.DataFrame({
        "week_key": [20261105, 20261105, 20261112],
        "song_key": [1, 2, 1],
        "rank": [10, 3, 8],
        "previous_rank": [12, None, 10],
        "weeks_on_chart": [5, 1, 6],
        "went_up": [True, False, None],
        "went_up_last_week": [True, False, True],
        "n_comments": [100, 20, 50],
        "n_comments_prev_week": [50, 0, 100],
        "avg_score": [0.4, -0.1, None],
        "lyrics_score": [None, 0.2, None],
    })


class FeaturesTest(unittest.TestCase):
    def test_columns_and_values(self):
        x = build_features(dataset())
        self.assertEqual(list(x.columns), FEATURES)
        self.assertEqual(x.loc[0, "rank_change"], 2)            # de 12 a 10: subió 2
        self.assertEqual(x.loc[1, "rank_change"], 0)            # debut: sin lugar anterior
        self.assertEqual(x.loc[1, "is_new"], 1)
        self.assertAlmostEqual(x.loc[0, "comments_growth"], np.log1p(100) - np.log1p(50))
        self.assertEqual(x.loc[2, "avg_score"], 0)               # sin reacción: neutro
        self.assertEqual(x.loc[0, "has_lyrics"], 0)
        self.assertEqual(x.loc[1, "lyrics_score"], 0.2)
        self.assertFalse(x.isna().any().any())

    def test_fingerprint_is_stable_and_changes_with_data(self):
        a = fingerprint(dataset())
        self.assertEqual(a, fingerprint(dataset()))
        changed = dataset()
        changed.loc[0, "n_comments"] = 101
        self.assertNotEqual(a, fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
