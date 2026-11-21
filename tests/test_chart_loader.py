"""Pruebas de la validación de ingestion/chart_loader.py (no necesitan la base).

Los CSV de Spotify no están en el repo, así que las pruebas arman un
chart de 200 filas a partir de una fila con el formato real.

    uv run python -m unittest discover -s tests -t .
"""

import unittest
from datetime import date

from ingestion.chart_loader import CHART_ROWS, InvalidFile, missing_ranks, parse_chart, parse_week

HEADER = "rank,uri,artist_names,track_name,source,peak_rank,previous_rank,weeks_on_chart,streams"


def uri(i: int) -> str:
    return "spotify:track:" + f"{i:022d}"


def chart(rows: list[str] | None = None, bom: bool = True) -> bytes:
    if rows is None:
        rows = [f'{i},"{uri(i)}","Fuerza Regida, Grand Theft Auto VI",'
                f'"Suzuki (from GTAVI: The Album)","Atlantic Records",1,{-1 if i % 10 == 0 else i},5,'
                f'"{10_000_000 - i}"' for i in range(1, CHART_ROWS + 1)]
    text = HEADER + "\n" + "\n".join(rows) + "\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


class ParseWeekTest(unittest.TestCase):
    def test_thursday(self):
        self.assertEqual(parse_week("regional-mx-weekly-2026-11-12.csv"), date(2026, 11, 12))
        self.assertEqual(parse_week("regional-mx-weekly-2026-11-12 (1).csv"), date(2026, 11, 12))

    def test_rejects_other_names_and_days(self):
        for name in ("regional-global-weekly-2026-11-12.csv",
                     "regional-mx-weekly-2026-11-13.csv",   # viernes
                     "regional-mx-weekly-2026-02-30.csv",   # no existe
                     "notas.csv"):
            with self.subTest(name=name), self.assertRaises(InvalidFile):
                parse_week(name)


class ParseChartTest(unittest.TestCase):
    def test_valid_chart(self):
        rows = parse_chart(chart())
        self.assertEqual(len(rows), CHART_ROWS)
        first = rows[0]
        self.assertEqual(first.rank, 1)
        self.assertEqual(first.artists, ("Fuerza Regida", "Grand Theft Auto VI"))
        self.assertEqual(first.streams, 9_999_999)
        self.assertEqual(first.source, "Atlantic Records")
        self.assertIsNone(rows[9].previous_rank)   # -1 en el CSV → None
        self.assertEqual(rows[10].previous_rank, 11)

    def test_without_bom(self):
        self.assertEqual(len(parse_chart(chart(bom=False))), CHART_ROWS)

    def test_bad_header(self):
        content = chart().replace(b"streams", b"plays", 1)
        with self.assertRaisesRegex(InvalidFile, "encabezado"):
            parse_chart(content)

    def test_tolerates_a_few_missing_ranks(self):
        # Como el CSV real del 2026-02-05, que no trae el lugar 34.
        lines = chart().decode("utf-8-sig").splitlines()[1:]
        rows = parse_chart(chart(lines[:33] + lines[34:]))
        self.assertEqual(len(rows), CHART_ROWS - 1)
        self.assertEqual(missing_ranks(rows), [34])
        self.assertEqual(missing_ranks(parse_chart(chart())), [])

    def test_too_many_missing_ranks(self):
        lines = chart().decode("utf-8-sig").splitlines()[1:]
        with self.assertRaisesRegex(InvalidFile, "194 filas"):
            parse_chart(chart(lines[:-6]))

    def test_repeated_rank_and_uri(self):
        lines = chart().decode("utf-8-sig").splitlines()[1:]
        lines[1] = lines[0]
        with self.assertRaisesRegex(InvalidFile, "rank repetido.*uri repetida"):
            parse_chart(chart(lines))

    def test_bad_values(self):
        lines = chart().decode("utf-8-sig").splitlines()[1:]
        lines[4] = lines[4].replace('"9999995"', '"mucho"')
        lines[5] = lines[5].replace(uri(6), "spotify:track:corto")
        with self.assertRaises(InvalidFile) as ctx:
            parse_chart(chart(lines))
        message = str(ctx.exception)
        self.assertIn("línea 6: streams no es un entero", message)
        self.assertIn("línea 7: uri inválida", message)

    def test_empty_source_is_none(self):
        lines = chart().decode("utf-8-sig").splitlines()[1:]
        lines[0] = lines[0].replace('"Atlantic Records"', '""')
        self.assertIsNone(parse_chart(chart(lines))[0].source)


if __name__ == "__main__":
    unittest.main()
