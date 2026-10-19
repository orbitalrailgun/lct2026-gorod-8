"""Вердикты экспериментального объекта для очереди диспетчера.

Эксперимент отвечает на вопрос цифрами, но цифры трудно потрогать.
Поэтому синтетический объект попадает и в ленту вердиктов — с теми же
карточками, уликами и хронологией, что настоящие. Разница одна: карточка
помечена как синтетическая, а объект исключён из всех счётчиков, отчётов
и метрик реальной сети.

Берётся конфигурация, которую эксперимент назвал рекомендуемой, и та же
обученная модель. Ничего специального для красоты не делается: если
на синтетическом объекте вердикт выглядит иначе, чем на реальном, —
значит, так и есть.

Запуск:  python -m scripts.13_experiment_verdicts
"""

import json
import os
import pickle

import duckdb

from core import (cards, config, etl, features, scoring, synthetic,
                  temperature)

TARGET = os.path.join(config.ROOT, "deploy", "seed_experiment_verdicts.json")
EXPERIMENT = os.path.join(config.MODEL_DIR, "sensor_experiment.json")

DAYS = 540
PICKETS = 400
SEED = 42
LIMIT = 12

FALLBACK = {
    "name": "полное оснащение",
    "sensors": {"Датчик дыма": 1, "Тепловой датчик": 2, "Датчик температуры": 4,
                "Датчик затопления": 6, "Состояние насоса": 10,
                "Датчик движения": 3, "КД Люк": 5, "Газовый датчик": 8},
    "polling": {"Датчик дыма": 30, "Датчик температуры": 15,
                "Датчик движения": 30},
    "controller_size": 8,
}


def recommended():
    """Конфигурация, которую назвал эксперимент.

    Правило выбора берётся из `core.cards`, то же самое, что показывает
    страница «Точность». Своё правило здесь было бы расхождением: страница
    рекомендовала бы одно, а очередь показывала бы вердикты другого
    оснащения, и объяснить это было бы нечем.
    """
    if not os.path.exists(EXPERIMENT):
        return FALLBACK
    with open(EXPERIMENT, encoding="utf-8") as fh:
        data = json.load(fh)
    best = cards.recommended(data["конфигурации"])
    if best is None:
        return FALLBACK
    return {
        "name": best["конфигурация"],
        "sensors": {k: int(v) for k, v in best["датчиков"].items()},
        "polling": {k: int(v) for k, v in best["опрос_минут"].items()},
        "controller_size": int(best["каналов_на_контроллер"]),
        # Порог срабатывания — одна из характеристик оснащения, и если
        # эксперимент назвал чувствительные приборы, вердикты обязаны
        # считаться с теми же порогами.
        "thresholds": best.get("пороги_срабатывания"),
    }


def main():
    calibration = synthetic.load_calibration()
    physics = synthetic.build_physics(calibration, days=DAYS, pickets=PICKETS,
                                      seed=SEED)
    choice = recommended()
    cfg = synthetic.configuration(choice["name"], choice["sensors"],
                                  polling=choice["polling"],
                                  self_diagnostics=True, line_monitoring=True,
                                  controller_size=choice["controller_size"],
                                  thresholds=choice.get("thresholds"))
    channels, journal = synthetic.observe(physics, cfg)
    print(f"конфигурация: {choice['name']}, каналов {len(channels)}, "
          f"записей журнала {len(journal):,}".replace(",", " "), flush=True)

    con = duckdb.connect()
    con.execute(f"SET memory_limit='{config.DUCKDB_MEMORY}'")
    con.execute(f"SET threads={config.DUCKDB_THREADS}")
    synthetic.stage(con, channels, journal)
    etl.build_fact_channel_day(con)
    # Разметка нужна до признаков: features собирается FROM labels.
    # Берётся она из физики, а не из наблюдений, иначе конфигурация
    # сама создавала бы себе положительные примеры.
    synthetic.build_labels(con, physics, channels)
    features.build_features(con)

    # Температурные витрины нужны карточке: scoring.temperature_evidence
    # читает temp_state и без неё падает. Строятся тем же кодом, что
    # на реальных данных, без ветки «если синтетика».
    temperature.build_temperature_day(con)
    temperature.build_neighbour_norm(con)
    temperature.build_temperature_norm(con)
    temperature.build_temperature_state(con)

    with open(os.path.join(config.MODEL_DIR, "m1_failure.pkl"), "rb") as fh:
        models = pickle.load(fh)["models"]

    # Сутки выбираются по максимуму отказов впереди: карточки должны
    # показывать работу сервиса, а не пустую ленту.
    day = con.execute("""
        SELECT d FROM features GROUP BY d ORDER BY sum(y) DESC, d DESC LIMIT 1
    """).fetchone()[0]

    verdicts = scoring.score_day(con, models, day, limit=LIMIT,
                                 with_precedent=False)
    for verdict in verdicts:
        verdict["synthetic"] = True
        verdict["card"]["synthetic"] = True
        verdict["card"]["experiment"] = choice["name"]
        verdict["object_name"] = synthetic.EXPERIMENT_OBJECT_NAME
        verdict["object_id"] = synthetic.EXPERIMENT_OBJECT_ID

    payload = {
        "конфигурация": choice["name"],
        "сутки": str(day),
        "каналов": len(channels),
        "вердиктов": len(verdicts),
        "вердикты": verdicts,
    }
    with open(TARGET, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)

    print(f"сутки {day}: вердиктов {len(verdicts)}")
    if verdicts:
        top = max(verdicts, key=lambda v: v["probability"])
        print(f"самый высокий риск: ПК{top['picket']} — {top['probability']:.0%}")
    print(f"сохранено: {TARGET}")


if __name__ == "__main__":
    main()
