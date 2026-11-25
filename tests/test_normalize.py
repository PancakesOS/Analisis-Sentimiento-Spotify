"""Pruebas de transform/normalize.py con nombres reales del chart del 12 de noviembre de 2026.

    uv run python -m unittest discover -s tests -t .
"""

import unittest

from transform.normalize import (normalize_artist, normalize_label, normalize_text,
                                 normalize_title, split_artists)


class NormalizeTextTest(unittest.TestCase):
    def test_accents_case_and_spaces(self):
        self.assertEqual(normalize_text("  Carín   León "), "carin leon")
        self.assertEqual(normalize_text("Ñengo Flow"), "nengo flow")
        self.assertEqual(normalize_text("Don’t"), "don't")

    def test_artist_same_key(self):
        self.assertEqual(normalize_artist("KAROL G"), normalize_artist("Karol G"))
        self.assertEqual(normalize_artist("Julión Álvarez y su Norteño Banda"),
                         "julion alvarez y su norteno banda")


class NormalizeTitleTest(unittest.TestCase):
    def test_removes_from_feat_with(self):
        self.assertEqual(normalize_title("Suzuki (from GTAVI: The Album)"), "suzuki")
        self.assertEqual(normalize_title("El Chivo (feat. T3r Elemento)"), "el chivo")
        self.assertEqual(normalize_title("/Perro Fiel (with benny blanco)"), "perro fiel")
        self.assertEqual(normalize_title("Canción feat. Alguien"), "cancion")

    def test_removes_dash_suffix(self):
        self.assertEqual(normalize_title("Tiroteo - Remix"), "tiroteo")
        self.assertEqual(normalize_title("Labios Rotos - En Vivo Desde México / 2010"), "labios rotos")
        self.assertEqual(normalize_title("Oye Mi Amor - 2019 Remasterizado"), "oye mi amor")

    def test_keeps_other_parentheses(self):
        self.assertEqual(normalize_title("Ando En La Disco (Da Players Town)"),
                         "ando en la disco (da players town)")

    def test_plain_titles(self):
        self.assertEqual(normalize_title("BbY WOW"), "bby wow")
        self.assertEqual(normalize_title("¿Y Qué?"), "y que")


class NormalizeLabelTest(unittest.TestCase):
    def assertSameLabel(self, *sources):
        values = {normalize_label(s) for s in sources}
        self.assertEqual(len(values), 1, f"{sources} → {values}")

    def test_variants_of_the_same_label(self):
        self.assertSameLabel("Josa Records", "Josa Records (Neton Vega)")
        self.assertSameLabel("Rico o Muerto", "Rico o Muerto Music", "Rico o Muerto by Baja Sound LLC")
        self.assertSameLabel("Double P Records", "2024 Double P Records")
        self.assertSameLabel("Bichota/Interscope Records",
                             "BICHOTA RECORDS / INTERSCOPE RECORDS / UNIVERSAL MUSIC LATINO")
        self.assertSameLabel("Rancho Humilde", "Rancho Humilde/Sony Music Latin",
                             "Rancho Humilde / DKid Records")

    def test_corporate_suffixes(self):
        self.assertEqual(normalize_label("Orfeón Videovox S.A. de C.V."), "orfeon videovox")
        self.assertEqual(normalize_label("TuStreams, LLC"), "tustreams")
        self.assertEqual(normalize_label("Universal Music Spain S.L."), "universal music spain")
        self.assertEqual(normalize_label("RCA Records Label"), "rca")

    def test_keeps_short_names_and_numbers(self):
        self.assertEqual(normalize_label("K Music/Sony Music Latin"), "k music")
        self.assertEqual(normalize_label("5050"), "5050")
        self.assertEqual(normalize_label("WEA Latina"), "wea latina")

    def test_empty(self):
        self.assertIsNone(normalize_label(""))
        self.assertIsNone(normalize_label("   "))
        self.assertIsNone(normalize_label(None))


class SplitArtistsTest(unittest.TestCase):
    def test_keeps_order(self):
        self.assertEqual(split_artists("KAROL G, Judeline, rusowsky"), ["KAROL G", "Judeline", "rusowsky"])
        self.assertEqual(split_artists("Fuerza Regida, Grand Theft Auto VI"),
                         ["Fuerza Regida", "Grand Theft Auto VI"])

    def test_names_with_comma(self):
        self.assertEqual(split_artists("Tyler, The Creator"), ["Tyler, The Creator"])
        self.assertEqual(split_artists("Kali Uchis, Tyler, The Creator, Bootsy Collins"),
                         ["Kali Uchis", "Tyler, The Creator", "Bootsy Collins"])
        self.assertEqual(split_artists("Earth, Wind & Fire, Emotions"),
                         ["Earth, Wind & Fire", "Emotions"])
        # Solo nombres completos: "The Creators" no es "The Creator".
        self.assertEqual(split_artists("Tyler, The Creators"), ["Tyler", "The Creators"])

    def test_removes_empty_and_repeated(self):
        self.assertEqual(split_artists("Neton Vega, , NETON VEGA, Fuerza Regida"),
                         ["Neton Vega", "Fuerza Regida"])
        self.assertEqual(split_artists(""), [])


if __name__ == "__main__":
    unittest.main()
