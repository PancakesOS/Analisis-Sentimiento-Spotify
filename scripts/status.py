"""Estado de los trabajos según sus logs: ¿corrió cada uno, cuándo y con errores?

    docker compose exec batch /app/.venv/bin/python -m scripts.status

Lee logs/<trabajo>.log (formato del RFC, sección 7 f) y muestra, por
trabajo, su última línea de fin, hace cuánto fue, si terminó con errores y
cuántos ERROR y WARNING dejó en las últimas 24 horas. Marca como
atrasado un trabajo que no ha terminado en más tiempo del que le toca, y
avisa si logs/cron_errors.log tiene algo (lo que un trabajo escribió a la
salida de errores).

Es lo primero que se corre cuando algo parece fallar; el manual de
operación (docs/operacion.md) dice qué hacer con cada caso.
"""

import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from common.jobs import LOG_DIR

MEXICO = ZoneInfo("America/Mexico_City")
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}) (\w+) [\w.]+: (.*)$")

# Cada cuánto debe terminar cada trabajo (con holgura); None = no tiene horario.
EXPECTED = {
    "chart_loader": timedelta(minutes=30),
    "video_finder": timedelta(hours=2),
    "stats_snapshot": timedelta(minutes=30),
    "comments_producer": timedelta(hours=2),
    "comments_processor": timedelta(hours=2),
    "aggregate_week": timedelta(hours=2),
    "lyrics_fetcher": timedelta(hours=2),
    "lyrics_processor": timedelta(hours=2),
    "retention": timedelta(hours=2),
    "train": timedelta(hours=2),
    "consumer": None,
    "verify": None,
    "check_idempotency": None,
    "label_sentiment": None,
}


def parse(path) -> list[tuple[datetime, str, str]]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE_RE.match(line.rstrip("\n"))
            if m:
                rows.append((datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z"),
                             m.group(2), m.group(3)))
    return rows


def ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // 1440} días"


def main() -> None:
    now = datetime.now(MEXICO)
    problems = 0
    print(f"Estado de los trabajos al {now:%d/%m/%Y %H:%M} (hora de México)\n")
    print(f"{'Trabajo':<20}{'Último fin':<18}{'Hace':>9}  {'Resultado':<14}{'ERROR 24 h':>11}"
          f"{'WARNING 24 h':>14}")
    for job, expected in EXPECTED.items():
        path = LOG_DIR / f"{job}.log"
        if not path.exists():
            if expected is not None:
                print(f"{job:<20}{'—':<18}{'':>9}  {'sin log':<14}")
                problems += 1
            continue
        rows = parse(path)
        ends = [r for r in rows if r[2].startswith("fin")]
        recent = [r for r in rows if now - r[0] <= timedelta(hours=24)]
        errors = sum(1 for r in recent if r[1] == "ERROR")
        warnings = sum(1 for r in recent if r[1] == "WARNING")
        if not ends:
            last, since, result = "—", "", "sin fin"
        else:
            t, _, msg = ends[-1]
            last, since = f"{t.astimezone(MEXICO):%d/%m %H:%M}", ago(now - t)
            m = re.search(r"errores=(\d+)", msg)
            result = "con errores" if (m and int(m.group(1)) > 0) or "DIFERENCIAS" in msg else "ok"
            if expected is not None and now - t > expected:
                result = "ATRASADO"
        if result in ("ATRASADO", "con errores", "sin fin") and expected is not None:
            problems += 1
        print(f"{job:<20}{last:<18}{since:>9}  {result:<14}{errors:>11}{warnings:>14}")

    cron_errors = LOG_DIR / "cron_errors.log"
    if cron_errors.exists() and cron_errors.stat().st_size > 0:
        tail = cron_errors.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        print("\nlogs/cron_errors.log no está vacío; últimas líneas:")
        for line in tail:
            print(f"  {line[:150]}")
        problems += 1

    print("\nATRASADO: no ha terminado en más tiempo del que le toca (¿la Mac estuvo dormida o el "
          "contenedor batch está detenido?).\nQué hacer con cada caso: docs/operacion.md")
    print("\nOK" if problems == 0 else f"\n{problems} cosa(s) por revisar")
    sys.exit(0 if problems == 0 else 1)


if __name__ == "__main__":
    main()
