"""Pruebas de la elección del video oficial y del mensaje de comentarios (no llaman a la API).

Los resultados de búsqueda imitan la respuesta real de search.list para
"Fuerza Regida Suzuki" del 16 de noviembre de 2026.

    uv run python -m unittest discover -s tests -t .
"""

import http.client
import unittest
import urllib.error
from unittest import mock

from ingestion import youtube_client
from ingestion.comments_producer import comment_message
from transform.match import choose_video
from transform.normalize import compact, search_title


def item(video_id: str, channel: str, title: str) -> dict:
    return {"id": {"kind": "youtube#video", "videoId": video_id},
            "snippet": {"channelTitle": channel, "title": title}}


SUZUKI = [
    item("mjC1QrkBlbY", "Amazing Lyrics", "Fuerza Regida - Suzuki (Letra/ Lyrics)"),
    item("wROPn_SFE5s", "Atlantic Records", "Fuerza Regida - Suzuki (From Grand Theft Auto VI: The Album)"),
    item("Fo00bEHNezY", "Tumbados Records", "Fuerza Regida - Suzuki (Official Audio)"),
]


class ChooseVideoTest(unittest.TestCase):
    def test_label_channel_beats_first_result(self):
        choice = choose_video(SUZUKI, "Suzuki (from GTAVI: The Album)",
                              ["Fuerza Regida", "Grand Theft Auto VI"], "atlantic")
        self.assertEqual((choice.video_id, choice.match, choice.rule), ("wROPn_SFE5s", "auto", "sello"))

    def test_artist_channel_first(self):
        items = [item("a", "Letras MX", "KAROL G - BbY WOW (Letra)"),
                 item("b", "KAROL G - Topic", "BbY WOW"),
                 item("c", "KarolGVEVO", "KAROL G, Judeline, rusowsky - BbY WOW (Official Video)")]
        choice = choose_video(items, "BbY WOW", ["KAROL G", "Judeline", "rusowsky"], "bichota")
        self.assertEqual((choice.video_id, choice.rule), ("c", "artista"))

    def test_topic_channel_when_nothing_better(self):
        items = [item("a", "Letras MX", "Olimpo (Letra)"), item("b", "Neton Vega - Topic", "Olimpo")]
        choice = choose_video(items, "Olimpo", ["Neton Vega", "Fuerza Regida"], "josa")
        self.assertEqual((choice.video_id, choice.rule, choice.match), ("b", "topic", "auto"))

    def test_title_must_match(self):
        # El canal del artista, pero con otra canción: no cuenta.
        items = [item("a", "Letras MX", "Neton Vega - Olimpo"), item("b", "Neton Vega", "Neton Vega - RIKI")]
        choice = choose_video(items, "Olimpo", ["Neton Vega"], "josa")
        self.assertEqual((choice.video_id, choice.match, choice.rule), ("a", "review", "primero"))

    def test_label_as_written_in_csv(self):
        # "DEL Records" se normaliza a "del" (muy corto), pero el canal es "DEL Records".
        items = [item("a", "Latin Union", "Lenin Ramírez - Polvo Rosita [Letra]"),
                 item("b", "DEL Records", "Polvo Rosita - (Video Oficial) - Lenin Ramirez - DEL Records 2024")]
        choice = choose_video(items, "Polvo Rosita", ["Lenin Ramírez"], "del", "DEL Records")
        self.assertEqual((choice.video_id, choice.rule), ("b", "sello"))

    def test_official_title_from_another_label(self):
        # El CSV dice "WEA Latina", pero el video oficial lo sube Rancho Humilde.
        items = [item("a", "Warner Música", "Junior H, Gael Valenzuela - DEMENCIA (Lyric Video)"),
                 item("b", "Rancho Humilde", "Junior H x Gael Valenzuela - DEMENCIA [Official Visualizer]")]
        choice = choose_video(items, "DEMENCIA", ["Junior H", "Gael Valenzuela"], "wea latina", "WEA Latina")
        self.assertEqual((choice.video_id, choice.rule, choice.match), ("b", "oficial", "auto"))

    def test_fan_channel_is_not_official(self):
        items = [item("a", "LatinHype", "Omar Camacho & Barbas - Dónde estás"),
                 item("b", "Victor Mendivil fans", "Barbas - Dónde estás (Video Oficial)")]
        choice = choose_video(items, "Dónde estás", ["Barbas", "Omar Camacho"], "iga", "IGA")
        self.assertEqual((choice.video_id, choice.match), ("a", "review"))

    def test_official_lyric_video_is_not_official_rule(self):
        items = [item("a", "Canal X", "Canción (Official Lyric Video)")]
        choice = choose_video(items, "Canción", ["Alguien"], None)
        self.assertEqual(choice.match, "review")

    def test_no_results(self):
        self.assertIsNone(choose_video([], "Olimpo", ["Neton Vega"], None))
        self.assertIsNone(choose_video([{"id": {"kind": "youtube#channel"}}], "Olimpo", ["Neton Vega"], None))


class SearchHelpersTest(unittest.TestCase):
    def test_search_title_keeps_versions(self):
        self.assertEqual(search_title("Suzuki (from GTAVI: The Album)"), "Suzuki")
        self.assertEqual(search_title("Tiroteo - Remix"), "Tiroteo - Remix")
        self.assertEqual(search_title("/Perro Fiel (with benny blanco)"), "Perro Fiel")

    def test_compact(self):
        self.assertEqual(compact("Karol G"), "karolg")
        self.assertEqual(compact("Carín León"), "carinleon")


class CommentMessageTest(unittest.TestCase):
    THREAD = {
        "kind": "youtube#commentThread", "id": "UgzHhqEQ4ykGI5MsoNN4AaABAg",
        "snippet": {
            "channelId": "UCe4LM_eKc9ywRmVuBm5pjQg", "videoId": "wROPn_SFE5s",
            "topLevelComment": {"id": "UgzHhqEQ4ykGI5MsoNN4AaABAg", "snippet": {
                "videoId": "wROPn_SFE5s", "textOriginal": "wey mi canción!!!",
                "textDisplay": "wey mi canción!!!",
                "authorDisplayName": "@alguien", "authorChannelId": {"value": "UCxyz"},
                "authorChannelUrl": "http://www.youtube.com/@alguien",
                "authorProfileImageUrl": "https://yt3.ggpht.com/foto",
                "likeCount": 1, "publishedAt": "2026-11-17T02:52:15Z", "updatedAt": "2026-11-17T02:52:15Z",
            }},
            "totalReplyCount": 0, "isPublic": True,
        },
    }

    def test_message_has_no_author_data(self):
        msg = comment_message(self.THREAD, "2026-11-21T20:00:00+00:00", "poll")
        self.assertEqual(msg["comment_id"], "UgzHhqEQ4ykGI5MsoNN4AaABAg")
        self.assertEqual(msg["text_original"], "wey mi canción!!!")
        self.assertEqual(msg["published_at"], "2026-11-17T02:52:15Z")
        self.assertFalse([k for k in msg if "author" in k.lower()])
        self.assertNotIn("alguien", repr(msg))
        self.assertNotIn("UCxyz", repr(msg))


class TransientErrorTest(unittest.TestCase):
    """Una respuesta cortada o sin red se reintenta y termina en NetworkError, sin mostrar la key."""

    def client(self):
        c = youtube_client.YouTubeClient.__new__(youtube_client.YouTubeClient)
        c.api_key = "CLAVE-SECRETA"
        c._reserve = lambda method: None
        return c

    def test_incomplete_read_is_network_error(self):
        failure = http.client.IncompleteRead(b"x" * 10, 100)
        with mock.patch("urllib.request.urlopen", side_effect=failure) as urlopen, \
                mock.patch("time.sleep"):
            with self.assertRaises(youtube_client.NetworkError):
                self.client().call("commentThreads.list", videoId="abc")
        self.assertEqual(urlopen.call_count, youtube_client.RETRIES + 1)

    def test_dns_failure_hides_the_key(self):
        failure = urllib.error.URLError("Name or service not known key=CLAVE-SECRETA")
        with mock.patch("urllib.request.urlopen", side_effect=failure), mock.patch("time.sleep"):
            with self.assertRaises(youtube_client.NetworkError) as ctx:
                self.client().call("videos.list", id="abc")
        self.assertNotIn("CLAVE-SECRETA", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
