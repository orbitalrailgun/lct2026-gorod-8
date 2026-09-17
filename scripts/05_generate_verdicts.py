"""День 3: расчёт вердиктов и выгрузка seed-файла для демонстрации.

Вердикты считаются здесь, на машине с данными, и сохраняются в компактный JSON.
В контейнер уезжает только он: приложение читает вердикты из PostgreSQL и не
нуждается ни в 313 млн событий, ни в матрице признаков. Благодаря этому
`docker compose up` поднимается за секунды, а образ остаётся небольшим.

Запуск:  .venv/bin/python -m scripts.05_generate_verdicts
"""

import json
import os

from core import config, etl, scoring

SEED = os.path.join(config.ROOT, "deploy", "seed_verdicts.json")
LAST_DAY = "2026-06-29"


def main():
    con = etl.connect()
    for m in ("dim_channel", "logical_domain", "features"):
        etl.load_mart(con, m)
    models = scoring.load_model()

    verdicts = scoring.score_day(con, models, LAST_DAY, limit=200)
    print(f"вердиктов на {LAST_DAY}: {len(verdicts)}")

    os.makedirs(os.path.dirname(SEED), exist_ok=True)
    with open(SEED, "w", encoding="utf-8") as fh:
        json.dump(verdicts, fh, ensure_ascii=False, indent=1)
    size = os.path.getsize(SEED) / 1024
    print(f"сохранено: {SEED} ({size:.0f} КБ)")

    top = verdicts[0]
    print(f"\nсамый высокий риск: {top['card']['location']} — {top['probability']:.0%}")


if __name__ == "__main__":
    main()
