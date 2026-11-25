"""Pruebas de la elección de la letra (match.choose_lyrics) y de su preparación para el modelo.

Los casos imitan respuestas reales de LRCLIB guardadas el 16 de noviembre
de 2026 (mismos títulos, artistas, ids y duraciones), pero con letras
inventadas: no se copia texto con derechos de autor.
"""

import unittest
from dataclasses import dataclass

from transform.lyrics_processor import combine, guess_language, split_stanzas
from transform.match import choose_lyrics, consensus_key, lyrics_text

VERSION_A = "Primera línea de prueba\nSegunda línea de prueba\nTercera línea\n\nCoro de prueba"
VERSION_B = "Otra forma de empezar\nSegunda línea distinta\nTercera línea\n\nCoro de prueba"


def record(id_, track, artist, duration, lyrics=VERSION_A, instrumental=False, synced=None):
    return {"id": id_, "trackName": track, "artistName": artist, "duration": duration,
            "instrumental": instrumental, "plainLyrics": lyrics, "syncedLyrics": synced}


class ChooseLyricsTest(unittest.TestCase):
    def test_suzuki_title_variants_and_most_common_duration(self):
        # 16 versiones con "Suzuki" y "Suzuki (from GTAVI: The Album)"; la duración más
        # común es 112 s y, entre esas, gana el id más bajo.
        results = [
            record(39870929, "Suzuki", "Fuerza Regida", 112.0),
            record(39212528, "Suzuki (from GTAVI: The Album)", "Fuerza Regida", 111.0),
            record(39259012, "Suzuki (from GTAVI: The Album)", "Fuerza Regida", 104.0),
            record(39099217, "Suzuki (from GTAVI: The Album)", "Fuerza Regida", 112.0),
            record(39046635, "suzuki", "Fuerza Regida, Grand Theft Auto VI", 112.0),
            record(39031075, "Suzuki (from GTAVI: The Album)", "Fuerza Regida, Grand Theft Auto VI",
                   111.0),
            record(39148940, "Suzuki (from GTAVI: The Album)", "Fuerza Regida", 151.0),
        ]
        choice = choose_lyrics(results, "Suzuki (from GTAVI: The Album)",
                               ["Fuerza Regida", "Grand Theft Auto VI"])
        self.assertEqual(choice.status, "found")
        self.assertEqual(choice.lrclib_id, 39046635)
        self.assertEqual(choice.candidates, 7)

    def test_bby_wow_consensus_group_wins_over_lower_id(self):
        # La versión minoritaria tiene un id más bajo, pero gana el grupo más grande.
        results = [record(37753694, "BbY WOW", "KAROL G", 226.0, lyrics=VERSION_B),
                   record(37669763, "BbY WOW", "KAROL G", 226.0),
                   record(39135738, "BbY WOW", "KAROL G", 202.0),
                   record(39261617, "BbY WOW", "KAROL G", 226.0)]
        choice = choose_lyrics(results, "BbY WOW", ["KAROL G"])
        self.assertEqual(choice.lrclib_id, 37669763)
        self.assertEqual(choice.group_size, 3)

    def test_order_of_results_does_not_matter(self):
        results = [record(3, "Olimpo", "Neton Vega", 150.0, lyrics=VERSION_B),
                   record(2, "Olimpo", "Neton Vega", 149.0)]
        a = choose_lyrics(results, "Olimpo", ["Neton Vega"])
        b = choose_lyrics(list(reversed(results)), "Olimpo", ["Neton Vega"])
        self.assertEqual(a, b)
        self.assertEqual(a.lrclib_id, 2)   # empate de grupos: el id más bajo

    def test_collaboration_listed_under_guest_artist(self):
        results = [record(10, "Olimpo", "Neton Vega & Fuerza Regida", 150.0)]
        self.assertEqual(choose_lyrics(results, "Olimpo", ["Fuerza Regida", "Neton Vega"]).lrclib_id, 10)

    def test_other_song_or_other_artist_is_ignored(self):
        results = [record(1, "Suzuki Remix Party", "Fuerza Regida", 112.0),
                   record(2, "Suzuki", "Otro Artista", 112.0)]
        self.assertIsNone(choose_lyrics(results, "Suzuki", ["Fuerza Regida"]))

    def test_instrumental(self):
        results = [record(5, "Intro", "Peso Pluma", 60.0, lyrics=None, instrumental=True)]
        choice = choose_lyrics(results, "Intro", ["Peso Pluma"])
        self.assertEqual((choice.status, choice.lrclib_id), ("instrumental", None))

    def test_synced_lyrics_only(self):
        synced = "[00:01.00] Primera línea de prueba\n[00:04.50] Segunda línea de prueba"
        rec = record(7, "Tema", "Artista", 100.0, lyrics="", synced=synced)
        self.assertEqual(lyrics_text(rec), "Primera línea de prueba\nSegunda línea de prueba")
        self.assertEqual(choose_lyrics([rec], "Tema", ["Artista"]).lrclib_id, 7)

    def test_consensus_key_ignores_case_accents_and_punctuation(self):
        self.assertEqual(consensus_key("¿Y MI BABY?\n\nOtra línea\nTercera"),
                         consensus_key("y mi baby\nOtra linea\nTERCERA\nCuarta"))


@dataclass
class FakeAnalysis:
    p_pos: float
    p_neu: float
    p_neg: float
    emotion: str


class LyricsTextTest(unittest.TestCase):
    def test_split_stanzas_by_blank_lines(self):
        self.assertEqual(split_stanzas("a b\nc d\n\n\ne f\n"), ["a b\nc d", "e f"])

    def test_long_block_is_cut_in_chunks_of_four_lines(self):
        text = "\n".join(f"línea {i}" for i in range(10))
        self.assertEqual([s.count("\n") + 1 for s in split_stanzas(text)], [4, 4, 2])

    def test_guess_language(self):
        self.assertEqual(guess_language("Yo te quiero y no me importa lo que digan de la vida"), "es")
        self.assertEqual(guess_language("I know you want me and I don't care what they say"), "en")
        self.assertEqual(guess_language("baby oh baby, tú eres mi vida"), "es")

    def test_combine_weights_by_words_and_ignores_others(self):
        stanzas = ["uno dos tres", "uno"]
        analyses = [FakeAnalysis(0.9, 0.05, 0.05, "joy"), FakeAnalysis(0.1, 0.1, 0.8, "others")]
        result = combine(stanzas, analyses)
        self.assertEqual(result["sentiment"], "POS")
        self.assertAlmostEqual(result["p_pos"], (3 * 0.9 + 0.1) / 4, places=4)
        self.assertEqual(result["emotion"], "joy")

    def test_combine_all_others(self):
        result = combine(["a"], [FakeAnalysis(0.2, 0.7, 0.1, "others")])
        self.assertEqual((result["sentiment"], result["emotion"]), ("NEU", "others"))


if __name__ == "__main__":
    unittest.main()
