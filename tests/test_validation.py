"""Pruebas de la validación manual (sentimiento y videos) y del aviso de chart atrasado."""

import unittest
from datetime import date

from scripts.label_sentiment import agreement
from scripts.review_videos import parse_video_id


class MissingChartsTest(unittest.TestCase):
    def test_counts_published_weeks(self):
        from delivery.dashboard.state import missing_charts
        end = date(2026, 11, 12)                                   # jueves
        self.assertEqual(missing_charts(end, date(2026, 11, 13)), 0)  # viernes: sale el del 12
        self.assertEqual(missing_charts(end, date(2026, 11, 19)), 0)  # jueves: aún no sale el siguiente
        self.assertEqual(missing_charts(end, date(2026, 11, 20)), 1)  # viernes: ya salió el del 19
        self.assertEqual(missing_charts(end, date(2026, 11, 24)), 1)
        self.assertEqual(missing_charts(end, date(2026, 11, 27)), 2)


class AgreementTest(unittest.TestCase):
    def test_empty(self):
        m = agreement([])
        self.assertEqual(m["n"], 0)
        self.assertIsNone(m["accuracy"])

    def test_perfect(self):
        m = agreement([("POS", "POS"), ("NEU", "NEU"), ("NEG", "NEG")])
        self.assertEqual(m["accuracy"], 1.0)
        self.assertAlmostEqual(m["kappa"], 1.0)

    def test_values(self):
        pairs = [("POS", "POS"), ("POS", "NEU"), ("NEU", "NEU"), ("NEG", "NEU")]
        m = agreement(pairs)
        self.assertEqual(m["n"], 4)
        self.assertEqual(m["accuracy"], 0.5)
        self.assertEqual(m["matrix"][("POS", "NEU")], 1)
        self.assertEqual(m["per_class"]["NEU"]["precision"], 1 / 3)   # 3 dichos neutros, 1 bien
        self.assertEqual(m["per_class"]["POS"]["recall"], 0.5)         # 2 positivos reales, 1 hallado
        self.assertIsNone(m["per_class"]["NEG"]["precision"])          # el modelo nunca dijo NEG
        # Esperada por azar: (2·1 + 1·3 + 1·0) / 16 = 5/16
        self.assertAlmostEqual(m["kappa"], (0.5 - 5 / 16) / (1 - 5 / 16))

    def test_all_same_class(self):
        m = agreement([("NEU", "NEU"), ("NEU", "NEU")])
        self.assertEqual(m["accuracy"], 1.0)
        self.assertIsNone(m["kappa"])   # sin variación no se puede corregir por azar


class VideoIdTest(unittest.TestCase):
    def test_formats(self):
        for answer in ("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3",
                       "https://youtu.be/dQw4w9WgXcQ", "youtube.com/shorts/dQw4w9WgXcQ",
                       "  dQw4w9WgXcQ  "):
            self.assertEqual(parse_video_id(answer), "dQw4w9WgXcQ")

    def test_invalid(self):
        for answer in ("", "no", "https://www.youtube.com/", "abc"):
            self.assertIsNone(parse_video_id(answer))


if __name__ == "__main__":
    unittest.main()
