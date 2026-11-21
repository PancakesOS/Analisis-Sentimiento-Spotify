"""Revisión manual de los videos elegidos por video_finder.

Listar los videos que hay que revisar (marcados como review, o sin video)
y una muestra al azar de los elegidos automáticamente, para el criterio de
éxito del RFC (20 canciones del top 50, al menos 90 % correctas):

    docker compose exec batch /app/.venv/bin/python -m scripts.review_videos

Corregir el video de una canción (queda como video_match = 'manual'):

    docker compose exec batch /app/.venv/bin/python -m scripts.review_videos \
        --set spotify:track:XXXXXXXXXXXXXXXXXXXXXX VIDEO_ID

Al corregirlo, comments_producer empieza a leer el video nuevo en su
siguiente ciclo. Los comentarios del video anterior ya no pertenecen a
ninguna canción, así que comments_processor los descarta (RFC, sección
7 c: SCD tipo 1 en dim_song).
"""

import argparse
import re
import sys

import psycopg

from common.jobs import pg_params

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def list_videos(conn, sample: int) -> None:
    latest = conn.execute("SELECT max(week_key) FROM fact_chart_week").fetchone()[0]
    print("Para revisar (review o sin video), del top 50 de las últimas 12 semanas:\n")
    rows = conn.execute(
        """
        WITH weeks AS (SELECT DISTINCT week_key FROM fact_chart_week ORDER BY week_key DESC LIMIT 12)
        SELECT s.spotify_uri, a.artist_name, s.track_name, s.youtube_video_id, s.video_match, min(f.rank)
        FROM fact_chart_week f JOIN weeks USING (week_key)
        JOIN dim_song s USING (song_key) JOIN dim_artist a ON a.artist_key = s.main_artist_key
        WHERE f.rank <= 50 AND (s.video_match = 'review' OR s.youtube_video_id IS NULL)
        GROUP BY 1, 2, 3, 4, 5 ORDER BY 6
        """).fetchall()
    for uri, artist, track, video, match, best in rows:
        url = f"https://www.youtube.com/watch?v={video}" if video else "(sin video; aún no se busca o no hubo resultados)"
        print(f"  mejor lugar {best:>3}  {artist} — {track}\n      {uri}\n      {url}")
    print(f"\nMuestra al azar de {sample} elegidos automáticamente del top 50 vigente ({latest}):\n")
    for uri, artist, track, video, rank in conn.execute(
            """
            SELECT s.spotify_uri, a.artist_name, s.track_name, s.youtube_video_id, f.rank
            FROM fact_chart_week f JOIN dim_song s USING (song_key)
            JOIN dim_artist a ON a.artist_key = s.main_artist_key
            WHERE f.week_key = %s AND f.rank <= 50 AND s.video_match = 'auto'
            ORDER BY md5(s.spotify_uri) LIMIT %s
            """, (latest, sample)):
        print(f"  lugar {rank:>3}  {artist} — {track}\n      https://www.youtube.com/watch?v={video}")


def set_video(conn, uri: str, video_id: str) -> None:
    if not VIDEO_ID_RE.match(video_id):
        sys.exit(f"«{video_id}» no parece un id de video de YouTube (11 caracteres)")
    with conn.transaction():
        row = conn.execute(
            "UPDATE dim_song s SET youtube_video_id = %s, video_match = 'manual', "
            "video_checked_at = now(), updated_at = now() "
            "FROM song_uri u WHERE u.spotify_uri = %s AND s.song_key = u.song_key "
            "RETURNING s.track_name", (video_id, uri)).fetchone()
    if row is None:
        sys.exit(f"No encontré la canción {uri}")
    print(f"Listo: «{row[0]}» ahora usa https://www.youtube.com/watch?v={video_id} (manual)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Revisión manual de los videos de YouTube")
    parser.add_argument("--set", nargs=2, metavar=("SPOTIFY_URI", "VIDEO_ID"))
    parser.add_argument("--muestra", type=int, default=20)
    args = parser.parse_args()
    with psycopg.connect(**pg_params(), connect_timeout=10, autocommit=True) as conn:
        if args.set:
            set_video(conn, *args.set)
        else:
            list_videos(conn, args.muestra)


if __name__ == "__main__":
    main()
