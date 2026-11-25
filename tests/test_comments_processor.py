"""Pruebas de la validación del crudo de comentarios y de la lectura por byte (checkpoint)."""

import json
import tempfile
import unittest
from pathlib import Path

from transform.comments_processor import InvalidLine, new_lines, parse_comment

VALID = {"comment_id": "Ugx1", "video_id": "wROPn_SFE5s", "text_original": "  Qué rolón  ",
         "like_count": 3, "reply_count": 0, "published_at": "2026-11-13T05:30:00Z",
         "updated_at": "2026-11-13T05:30:00Z", "fetched_at": "2026-11-13T06:00:00+00:00",
         "source": "poll"}


def line(**changes) -> str:
    return json.dumps({**VALID, **changes}, ensure_ascii=False)


class ParseCommentTest(unittest.TestCase):
    def test_valid_comment(self):
        c = parse_comment(line())
        self.assertEqual(c["comment_id"], "Ugx1")
        self.assertEqual(c["text"], "Qué rolón")      # sin espacios a los lados
        self.assertEqual(c["like_count"], 3)

    def test_day_is_mexico_time(self):
        # 05:30 UTC del 13 de noviembre son las 23:30 del 12 en México: cuenta para el día 12
        # (y para la semana del chart que termina el jueves 12).
        self.assertEqual(parse_comment(line())["date_key"], 20261112)

    def test_invalid_json(self):
        with self.assertRaises(InvalidLine) as ctx:
            parse_comment('{"comment_id": "x"')
        self.assertEqual(ctx.exception.reason, "json_invalido")
        with self.assertRaises(InvalidLine):
            parse_comment("[1, 2]")

    def test_missing_or_wrong_fields(self):
        for bad in (line(comment_id=""), line(video_id=None), line(text_original=5),
                    line(like_count=-1), line(reply_count=True), line(published_at="ayer"),
                    line(published_at="2026-11-13T05:30:00")):   # sin zona horaria
            with self.subTest(bad=bad), self.assertRaises(InvalidLine) as ctx:
                parse_comment(bad)
            self.assertEqual(ctx.exception.reason, "campos_invalidos")

    def test_empty_text(self):
        with self.assertRaises(InvalidLine) as ctx:
            parse_comment(line(text_original="   \n "))
        self.assertEqual(ctx.exception.reason, "vacio")

    def test_null_character_removed(self):
        self.assertEqual(parse_comment(line(text_original="hola\x00 mundo"))["text"], "hola mundo")

    def test_author_fields_are_never_read(self):
        c = parse_comment(line(authorDisplayName="Alguien", authorChannelId="UC123"))
        self.assertNotIn("authorDisplayName", c)
        self.assertNotIn("authorChannelId", c)


class NewLinesTest(unittest.TestCase):
    def test_reads_only_complete_lines_and_reports_byte_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-11-21.log"
            path.write_bytes("uno\ndós\ntres a medias".encode("utf-8"))
            lines = new_lines(path, 0)
            self.assertEqual([text for text, _ in lines], ["uno", "dós"])
            end = lines[-1][1]
            self.assertEqual(end, len("uno\ndós\n".encode("utf-8")))
            # Cuando el consumer termina la línea, la siguiente lectura empieza donde se quedó.
            with path.open("ab") as f:
                f.write(b"\ncuatro\n")
            self.assertEqual([text for text, _ in new_lines(path, end)], ["tres a medias", "cuatro"])


if __name__ == "__main__":
    unittest.main()
