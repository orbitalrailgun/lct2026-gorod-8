"""Подготовка данных для диаграмм.

Диаграммы качества модели и наблюдаемости строятся на величинах, которых
в контейнере нет: витрины и обучающая выборка остаются на машине с данными.
Поэтому нужные агрегаты считаются здесь один раз и уезжают в образ
компактным JSON — тем же способом, что и вердикты.

Оперативные диаграммы (вердикты по объектам, вызревание признаков) так
не готовятся: они считаются на лету из PostgreSQL, потому что меняются
с каждым квитированием.

Запуск:  python -m scripts.09_build_analytics
"""

import json
import os

import duckdb

from core import config

TARGET = os.path.join(config.ROOT, "deploy", "seed_analytics.json")

HORIZON_LABELS = {24: "24 часа", 72: "3 суток", 168: "7 суток", 336: "14 суток"}

# Типы датчиков, отсутствие которых означает конкретную слепоту сервиса.
CONTROL_TYPES = [
    ("Тепловой датчик", "тепловой контроль"),
    ("Датчик затопления", "контроль затопления"),
    ("Состояние насоса", "контроль насосов"),
    ("Датчик температуры", "температурный контроль"),
    ("Датчик дыма", "контроль задымления"),
]


def load_json(name):
    path = os.path.join(config.MODEL_DIR, name)
    if not os.path.exists(path):
        print(f"нет файла {path}, пропуск")
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def horizon_rows():
    """Достижимая точность по горизонтам — из прогона scripts/04."""
    sweep = load_json("horizon_sweep.json") or []
    rows = []
    for item in sweep:
        hours = item["horizon_hours"]
        rows.append({
            "label": HORIZON_LABELS.get(hours, f"{hours} ч"),
            "hours": hours,
            # Плотность головы очереди — то, что реально означает качество
            # для смены. «Максимальная точность на кривой» оставлена рядом
            # для полноты, но на диаграмму не выводится: она достигается
            # на единицах предупреждений и ни о чём не говорит.
            "precision_at_50": item["precision@50"],
            "precision_at_100": item["precision@100"],
            "best_precision": item["best_precision"],
            "positives": item["positives"],
            "base_rate": item["base_rate"],
            "target_met": item["target_precision_achieved"],
        })
    return sorted(rows, key=lambda r: r["hours"])


def recall_rows():
    """Цена полноты — из прогона, измерявшего нагрузку на смену."""
    math = load_json("recall_math.json")
    if not math:
        return []
    return math["recall_curve"]


def observability_rows(con):
    """Сколько объектов сети лишены каждого вида контроля."""
    total = con.execute(
        "SELECT count(DISTINCT parent_name) FROM dim_channel").fetchone()[0]
    rows = []
    for sensor_type, title in CONTROL_TYPES:
        n = con.execute(f"""
            SELECT count(*) FROM (
                SELECT parent_name,
                       count(*) FILTER (WHERE sensor_type = '{sensor_type}') AS n
                FROM dim_channel GROUP BY parent_name
            ) WHERE n = 0
        """).fetchone()[0]
        rows.append({"type": title, "objects_without": n})
    rows.sort(key=lambda r: -r["objects_without"])
    return total, rows


def facts(con):
    """Числа для плашек: те же, что в записке, из того же источника."""
    one = lambda sql: con.execute(sql).fetchone()[0]
    return {
        "channels": one("SELECT count(*) FROM dim_channel"),
        "orphans": one("SELECT count(*) FROM channel_profile")
                   - one("SELECT count(*) FROM dim_channel"),
        "objects": one("SELECT count(DISTINCT parent_name) FROM dim_channel"),
        "domains": one("SELECT count(*) FROM logical_domain"),
        "domains_single": one(
            "SELECT count(*) FROM logical_domain WHERE n_channels = 1"),
        "fire_local": one(
            "SELECT count(*) FROM fire_candidate WHERE NOT is_sweep"),
        "fire_no_thermal": one(
            "SELECT count(*) FROM fire_candidate WHERE NOT is_sweep AND NOT has_thermal"),
        "guards": one("SELECT count(*) FROM guard_health"),
        "guards_stuck": one("SELECT count(*) FROM guard_health WHERE is_stuck"),
    }


def main():
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{config.DUCKDB_MEMORY}'")
    con.execute(f"SET threads={config.DUCKDB_THREADS}")
    for name in ("dim_channel", "channel_profile", "logical_domain",
                 "fire_candidate", "guard_health"):
        path = os.path.join(config.MART_DIR, f"{name}.parquet")
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM '{path}'")

    total_objects, observ = observability_rows(con)
    payload = {
        "horizon": horizon_rows(),
        "recall": recall_rows(),
        "observability": {"objects": total_objects, "rows": observ},
        "facts": facts(con),
    }

    with open(TARGET, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print(f"горизонтов: {len(payload['horizon'])}")
    print(f"точек кривой полноты: {len(payload['recall'])}")
    print(f"видов контроля: {len(observ)} на {total_objects} объектах")
    print(f"сохранено: {TARGET} ({os.path.getsize(TARGET) / 1024:.0f} КБ)")


if __name__ == "__main__":
    main()
