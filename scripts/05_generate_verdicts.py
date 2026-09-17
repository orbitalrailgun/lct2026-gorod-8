"""День 3: расчёт вердиктов и выгрузка seed-файла для демонстрации.

Вердикты считаются здесь, на машине с данными, и сохраняются в компактный JSON.
В контейнер уезжает только он: приложение читает вердикты из PostgreSQL и не
нуждается ни в 313 млн событий, ни в матрице признаков. Благодаря этому
`docker compose up` поднимается за секунды, а образ остаётся небольшим.

Запуск:  .venv/bin/python -m scripts.05_generate_verdicts
"""

import json
import os

from core import cards, config, etl, geo, scoring

SEED = os.path.join(config.ROOT, "deploy", "seed_verdicts.json")
GEOMETRY = os.path.join(config.ROOT, "deploy", "seed_geometry.json")
# Демонстрационные сутки выбраны как единственные в последнем квартале данных,
# где одновременно присутствуют все три сценария: 12 аномальных насосов и 30 очагов
# задымления. На случайно взятых сутках подтопление и пожар дают по одному вердикту —
# события редкие, и это само по себе честная характеристика предметной области.
LAST_DAY = "2026-05-06"


def main():
    con = etl.connect()
    for m in ("dim_channel", "logical_domain", "features"):
        etl.load_mart(con, m)
    models = scoring.load_model()

    for m in ("pump_anomaly", "pump_network", "fire_candidate",
              "intrusion_event", "fact_channel_day"):
        etl.load_mart(con, m)

    verdicts = scoring.score_day(con, models, LAST_DAY, limit=120)
    print(f"M1 отказы:      {len(verdicts):>4}")
    pumps_v = scoring.score_pumps(con, LAST_DAY, limit=40)
    print(f"M2 подтопление: {len(pumps_v):>4}")
    fire_v = scoring.score_fire(con, LAST_DAY, limit=20)
    print(f"M3 пожар:       {len(fire_v):>4}")
    intr_v = scoring.score_intrusion_day(con, LAST_DAY, limit=20)
    print(f"M4 проникновение: {len(intr_v):>2}")
    verdicts = verdicts + pumps_v + fire_v + intr_v
    verdicts.sort(key=lambda r: -r["probability"])

    # заголовок, метки появления признаков, таймлайн
    for v in verdicts:
        cards.enrich_card(con, v, LAST_DAY)
    print(f"всего на {LAST_DAY}: {len(verdicts)}")

    multi = cards.group_by_object(verdicts)
    print(f"мультикарточек по объектам: {len(multi)}")

    # Геометрия для карты считается здесь: приложению витрины не нужны.
    channel_rows = con.execute("""
        SELECT parent_name, object_name, sensor_name, picket FROM dim_channel
    """).df().to_dict("records")
    geometry = geo.build_object_geometry(multi, channel_rows)
    with open(GEOMETRY, "w", encoding="utf-8") as fh:
        json.dump(geometry, fh, ensure_ascii=False, indent=1)
    known = sum(1 for g in geometry if g["anchor_known"])
    print(f"геометрия: {len(geometry)} объектов, из них с реальным районом {known}")
    inc = [v["card"]["incubation_days"] for v in verdicts
           if v["card"].get("incubation_days") is not None]
    if inc:
        print(f"вызревание признаков: медиана {sorted(inc)[len(inc)//2]} сут, "
              f"максимум {max(inc)} сут")

    os.makedirs(os.path.dirname(SEED), exist_ok=True)
    with open(SEED, "w", encoding="utf-8") as fh:
        json.dump(verdicts, fh, ensure_ascii=False, indent=1)
    size = os.path.getsize(SEED) / 1024
    print(f"сохранено: {SEED} ({size:.0f} КБ)")

    top = verdicts[0]
    print(f"\nсамый высокий риск: {top['card']['location']} — {top['probability']:.0%}")


if __name__ == "__main__":
    main()
