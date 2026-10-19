"""Калибровка датчиков: что измерено на реальных данных перед синтезом.

Организаторы спрашивают, какой набор датчиков и с какими характеристиками
нужно поставить, чтобы точность достигла требуемой. Отвечать на это
выдумкой нельзя, поэтому сначала измеряется то, что есть: как ведут себя
датчики каждого типа, как часто они отказывают, чем отличается канал
перед отказом и какая группа признаков реально держит прогноз.

Файл `models/sensor_calibration.json` — основание для двух вещей сразу:
генератор экспериментального объекта берёт отсюда распределения, а
рекомендация по дооснащению — измеренные вклады.

Главный результат, ради которого затевался замер, оказался отрицательным
и приведён здесь же: оснащённость точки не улучшает прогноз отказа.
Признаки соседей дают вклад ×2,1, собственные события канала — ×908.

Память: отложенная выборка читается порциями, оценки хранятся во float32.
Абляция прогоняет модель шесть раз, это несколько минут.

Запуск:  python -m scripts.11_calibrate_sensors
"""

import json
import os
import pickle

import numpy as np
import pandas as pd

from core import config, etl, model
from core.features import FEATURE_NAMES

TARGET = os.path.join(config.MODEL_DIR, "sensor_calibration.json")

# Группы признаков по источнику наблюдаемости. Именно они отвечают
# на вопрос «что даёт дооснащение»: каждая группа — это то, что появляется
# или исчезает вместе с датчиками и их характеристиками.
GROUPS = {
    "окружение": {
        "features": ["domain_size", "domain_dev_share_7d", "domain_silent_share"],
        "смысл": "соседние датчики в той же точке",
        "даёт": "подтверждение события вторым прибором",
    },
    "инфраструктура": {
        "features": ["base_failed_share_7d"],
        "смысл": "состояние других каналов того же контроллера",
        "даёт": "видимость каскада по питающей линии",
    },
    "связь": {
        "features": ["undefined_ev_7d", "undefined_share_7d"],
        "смысл": "потери связи с каналом",
        "даёт": "контроль линии до прибора",
    },
    "молчание": {
        "features": ["silence_days", "silence_ratio", "active_days_7d"],
        "смысл": "регулярность выхода на связь",
        "даёт": "периодичность опроса",
    },
    "самодиагностика": {
        "features": ["fault_ev_7d", "fault_days_30d", "fault_ratio_90d",
                     "days_since_fault"],
        "смысл": "собственные сообщения прибора о неисправности",
        "даёт": "прибор, умеющий сообщать о своём состоянии",
    },
}

PRECURSOR_FEATURES = ["fault_ev_7d", "fault_days_30d", "undefined_ev_7d",
                      "ev_7d", "dev_days_7d", "domain_dev_share_7d",
                      "base_failed_share_7d", "silence_days"]


def connect():
    con = etl.connect()
    for mart in ("dim_channel", "fact_channel_day", "labels", "features",
                 "pump_day", "fire_candidate", "intrusion_event", "temp_day",
                 "logical_domain"):
        etl.load_mart(con, mart)
    return con


def sensor_behaviour(con):
    """Как ведёт себя канал каждого типа: активность и частота отказа."""
    activity = con.execute("""
        WITH span AS (
            SELECT channel_id, count(*) AS active_days,
                   date_diff('day', min(d), max(d)) + 1 AS life_days,
                   avg(n_ev) AS avg_events, median(n_ev) AS med_events
            FROM fact_channel_day GROUP BY 1)
        SELECT c.sensor_type,
               count(DISTINCT s.channel_id)                        AS channels,
               round(avg(s.avg_events), 2)                         AS events_per_active_day,
               round(median(s.med_events), 1)                      AS median_events,
               round(avg(s.active_days * 1.0 / nullif(s.life_days, 0)), 4) AS active_share
        FROM span s JOIN dim_channel c USING (channel_id)
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf()

    failures = con.execute(f"""
        SELECT c.sensor_type, count(*) AS channel_days, sum(l.y) AS failures,
               round(sum(l.y) * 1.0 / count(*), 6) AS daily_failure_rate
        FROM labels l JOIN dim_channel c USING (channel_id)
        WHERE l.d >= DATE '{model.TEST_FROM}'
        GROUP BY 1 ORDER BY 4 DESC
    """).fetchdf()

    merged = activity.merge(failures, on="sensor_type", how="left")
    return merged.fillna(0).to_dict("records")


def precursor_profile(con):
    """Во сколько раз признак выше обычного в сутки перед отказом."""
    parts = ", ".join(
        f"avg(CASE WHEN y = 1 THEN {f} END) AS \"{f}_pos\", "
        f"avg(CASE WHEN y = 0 THEN {f} END) AS \"{f}_neg\""
        for f in PRECURSOR_FEATURES)
    row = con.execute(f"""
        SELECT {parts} FROM features WHERE d >= DATE '{model.TEST_FROM}'
    """).fetchdf().iloc[0]

    profile = {}
    for f in PRECURSOR_FEATURES:
        before, usual = float(row[f"{f}_pos"]), float(row[f"{f}_neg"])
        profile[f] = {"перед отказом": round(before, 4),
                      "обычно": round(usual, 4),
                      "отношение": round(before / usual, 1) if usual else None}

    # Насколько рано сигнал вообще виден: это задаёт горизонт, на котором
    # дооснащение может что-то изменить.
    early = con.execute(f"""
        WITH fails AS (
            SELECT channel_id, d AS fail_day FROM features
            WHERE y = 1 AND d >= DATE '{model.TEST_FROM}'),
        probe AS (
            SELECT x.horizon,
                   max(CASE WHEN f.fault_ev_7d > 0 THEN 1 ELSE 0 END) AS had_fault,
                   max(CASE WHEN f.undefined_ev_7d > 0 THEN 1 ELSE 0 END) AS had_undefined
            FROM fails
            CROSS JOIN (VALUES (1), (3), (7), (14), (30)) AS x(horizon)
            JOIN features f ON f.channel_id = fails.channel_id
                           AND f.d = fails.fail_day - x.horizon
            GROUP BY fails.channel_id, x.horizon)
        SELECT horizon, round(avg(had_fault), 4) AS with_fault,
               round(avg(had_undefined), 4) AS with_undefined
        FROM probe GROUP BY 1 ORDER BY 1
    """).fetchdf().to_dict("records")

    return {"признаки": profile, "ранний_сигнал": early}


def group_ablation(con):
    """Что держит прогноз: вклад каждой группы наблюдаемости.

    Группа «отключается» подстановкой значений, которыми модель кодирует
    отсутствие источника. Разница в precision@k и есть ответ на вопрос,
    что даёт соответствующее оснащение.
    """
    with open(os.path.join(config.MODEL_DIR, "m1_failure.pkl"), "rb") as fh:
        forest = pickle.load(fh)["models"]["forest"]

    cols = ", ".join(FEATURE_NAMES)
    sql = (f"SELECT y, {cols} FROM features "
           f"WHERE d BETWEEN DATE '{model.TEST_FROM}' AND DATE '{model.TEST_TO}' "
           f"ORDER BY channel_id, d")
    total = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]

    chunk, parts = 500_000, []
    for offset in range(0, total, chunk):
        df = con.execute(f"{sql} LIMIT {chunk} OFFSET {offset}").df()
        if df.empty:
            break
        parts.append(df)
    data = pd.concat(parts, ignore_index=True)
    y = data["y"].values
    X = data[FEATURE_NAMES].astype("float32")
    X = X.replace([np.inf, -np.inf], np.nan).fillna(model.MISSING)

    def precision_at(scores, k):
        return float(y[np.argsort(-scores)[:k]].mean())

    base = forest.predict_proba(X.values)[:, 1]
    result = {"исходно": {"precision@50": precision_at(base, 50),
                          "precision@100": precision_at(base, 100)},
              "группы": {}}

    for name, spec in GROUPS.items():
        muted = X.copy()
        for feature in spec["features"]:
            muted[feature] = (model.MISSING
                              if feature in ("fault_ratio_90d", "silence_ratio")
                              else 0.0)
        if "domain_size" in spec["features"]:
            muted["domain_size"] = 1.0      # одинокий канал в точке
        scores = forest.predict_proba(muted.values)[:, 1]
        result["группы"][name] = {
            "смысл": spec["смысл"], "даёт": spec["даёт"],
            "precision@50": precision_at(scores, 50),
            "precision@100": precision_at(scores, 100),
            "потеря@50": round(result["исходно"]["precision@50"]
                               - precision_at(scores, 50), 4),
        }
        print(f"  {name:<18} precision@50 {precision_at(scores, 50):.2%}", flush=True)

    return result


def instrumentation(con):
    """Сколько типов датчиков стоит в точке и как это связано с качеством."""
    return con.execute("""
        SELECT n_types, count(*) AS points, sum(n_channels) AS channels
        FROM (SELECT object_id, picket,
                     count(DISTINCT sensor_type) AS n_types,
                     count(*) AS n_channels
              FROM dim_channel WHERE picket IS NOT NULL GROUP BY 1, 2)
        GROUP BY 1 ORDER BY 1
    """).fetchdf().to_dict("records")


def incident_processes(con):
    """Распределения инцидентных процессов для генератора."""
    pumps = con.execute("""
        SELECT round(avg(n_starts), 2) AS mean, median(n_starts) AS median,
               quantile_cont(n_starts, 0.90) AS p90,
               quantile_cont(n_starts, 0.99) AS p99, max(n_starts) AS max
        FROM pump_day WHERE n_starts > 0
    """).fetchdf().iloc[0].to_dict()
    pumps["по месяцам"] = con.execute("""
        SELECT month(d) AS month, round(avg(n_starts), 2) AS starts
        FROM pump_day GROUP BY 1 ORDER BY 1
    """).fetchdf().to_dict("records")

    smoke = con.execute("""
        SELECT is_sweep, count(*) AS episodes,
               round(avg(n_smoke_ch), 2) AS channels,
               round(avg(n_smoke_ev), 2) AS triggers,
               round(avg(CASE WHEN has_thermal THEN 1 ELSE 0 END), 4) AS with_thermal,
               round(avg(domain_size), 2) AS point_channels
        FROM fire_candidate GROUP BY 1
    """).fetchdf().to_dict("records")

    intrusion = con.execute("""
        SELECT round(avg(n_triggers), 2) AS triggers, median(n_triggers) AS median,
               round(avg(n_types), 2) AS types,
               round(avg(date_diff('minute', first_t, last_t)), 1) AS minutes,
               round(avg(CASE WHEN is_night THEN 1 ELSE 0 END), 4) AS night_share
        FROM intrusion_event
    """).fetchdf().iloc[0].to_dict()

    temperature = con.execute("""
        SELECT round(avg(t_avg), 2) AS mean, round(avg(t_max - t_min), 2) AS daily_range,
               round(stddev(t_avg), 2) AS spread_between_points,
               round(avg(n_channels), 2) AS sensors_per_point
        FROM temp_day
    """).fetchdf().iloc[0].to_dict()

    domains = con.execute("""
        SELECT n_channels AS channels, count(*) AS domains
        FROM logical_domain GROUP BY 1 ORDER BY 1
    """).fetchdf().to_dict("records")

    return {"насосы": pumps, "задымление": smoke, "проникновение": intrusion,
            "температура": temperature, "домены": domains}


def main():
    con = connect()

    print("поведение датчиков по типам…", flush=True)
    behaviour = sensor_behaviour(con)

    print("профиль предвестника отказа…", flush=True)
    precursor = precursor_profile(con)

    print("оснащённость точек…", flush=True)
    points = instrumentation(con)

    print("инцидентные процессы…", flush=True)
    incidents = incident_processes(con)

    print("вклад групп наблюдаемости (абляция, несколько минут)…", flush=True)
    ablation = group_ablation(con)

    payload = {
        "измерено": "на поставленных данных, отложенный период "
                    f"{model.TEST_FROM}..{model.TEST_TO}",
        "поведение_датчиков": behaviour,
        "предвестник_отказа": precursor,
        "оснащённость_точек": points,
        "инцидентные_процессы": incidents,
        "вклад_наблюдаемости": ablation,
    }
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    with open(TARGET, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print(f"\nсохранено: {TARGET}")
    print(f"типов датчиков измерено: {len(behaviour)}")
    worst = min(ablation["группы"].items(), key=lambda kv: kv[1]["precision@50"])
    print(f"сильнее всего прогноз держит: {worst[0]} "
          f"(без неё precision@50 падает до {worst[1]['precision@50']:.0%})")


if __name__ == "__main__":
    main()
