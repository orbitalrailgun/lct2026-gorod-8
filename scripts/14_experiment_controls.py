"""Контроли честности: что именно предсказывает модель отказов.

Эксперимент с дооснащением опирается на измеренный вклад групп признаков,
и самая сильная группа — самодиагностика прибора. Но целевая переменная
M1 — это «канал выдаст значение класса ОТКАЗ в следующие сутки», а самый
сильный признак — «сколько таких значений было за прошлые 7 суток».
Это один и тот же поток событий, сдвинутый по времени. Отсюда законный
вопрос: мы предсказываем будущую поломку или продолжение уже идущей?

Скрипт отвечает числом. Отложенная выборка делится на две популяции:

  «новый отказ»     — на момент прогноза канал не сообщал о неисправности
                      (fault_ev_7d = 0): предсказать его действительно нужно;
  «продолжение»     — канал уже в отказном состоянии (fault_ev_7d > 0):
                      здесь прогноз близок к констатации.

Метрики считаются внутри каждой популяции отдельно — ранжирование тоже,
иначе одна популяция вытеснит другую из головы очереди и сравнение
потеряет смысл.

Второй контроль — размеры групп по оснащённости точки. Утверждение
«богатые точки не предсказываются лучше» опирается на хвост, где
17 точек и 1 точка; без этих чисел утверждение выглядит сильнее,
чем оно есть.

Запуск:  python -m scripts.14_experiment_controls
"""

import json
import os
import pickle

import numpy as np

from core import config, etl, labels, model
from core.features import FEATURE_NAMES

TARGET = os.path.join(config.MODEL_DIR, "experiment_controls.json")


def connect():
    con = etl.connect()
    for mart in ("dim_channel", "fact_channel_day", "labels", "features"):
        etl.load_mart(con, mart)
    return con


def own_fault_window(con):
    """Сообщения прибора о неисправности за 7 суток БЕЗ массовых базо-дней.

    Зачем отдельное окно. Признак модели `fault_ev_7d` суммирует отказные
    события канала без разбора, а целевая переменная массовые базо-дни
    исключает: `core/labels.py` считает их следами работы бригады, а не
    отказом прибора. Из-за этой асимметрии популяция «канал сообщал
    о неисправности» склеивает две разные вещи:

      свой отказ          — события вне массовых суток, 0,3 % канало-суток;
      только массовый день — события были, но все пришлись на сутки,
                             когда на базе лежало сразу много каналов.

    Без разделения точность, заработанная в первом страте, делится
    на базовую частоту, разбавленную вторым, — и получается число,
    которое записка сама же отвергает двумя абзацами ниже, когда
    объясняет, почему выигрыш ×339 обманчив.
    """
    labels.build_sweep_days(con)
    con.execute("""
        CREATE OR REPLACE TABLE own_fault AS
        WITH clean AS (
            SELECT f.channel_id, f.d,
                   CASE WHEN s.d IS NULL
                        THEN COALESCE(f.n_fault, 0) + COALESCE(f.n_disabled, 0)
                        ELSE 0 END AS own_events
            FROM fact_channel_day f
            JOIN dim_channel c ON c.channel_id = f.channel_id
            LEFT JOIN sweep_day s ON s.tag_base = c.tag_base AND s.d = f.d
        )
        SELECT channel_id, d,
               sum(own_events) OVER (
                   PARTITION BY channel_id ORDER BY d
                   ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS own_fault_7d
        FROM clean
    """)


def load_model():
    with open(os.path.join(config.MODEL_DIR, "m1_failure.pkl"), "rb") as fh:
        return pickle.load(fh)["models"]


def population_metrics(df, scores, mask, k_values=(50, 100)):
    """Метрики внутри одной популяции с ранжированием внутри неё же."""
    y = df["y"].values[mask]
    s = scores[mask]
    if not len(y) or not y.sum():
        return {"канало-суток": int(len(y)), "позитивов": int(y.sum())}
    out = {
        "канало-суток": int(len(y)),
        "позитивов": int(y.sum()),
        "базовая_частота": float(y.mean()),
    }
    order = np.argsort(-s)
    for k in k_values:
        if len(order) < k:
            continue
        hits = int(y[order[:k]].sum())
        out[f"precision@{k}"] = hits / k
        out[f"lift@{k}"] = (hits / k) / out["базовая_частота"]
    return out


def queue_composition(df, scores, fresh, k_values=(50, 100, 500)):
    """Состав головы очереди по общему ранжированию.

    Это самое прямое доказательство и самое понятное эксплуатации.
    Метрики внутри популяций отвечают на вопрос «а если бы диспетчер
    смотрел только на такие каналы». Но диспетчер смотрит на одну общую
    очередь, и вопрос у него другой: кто вообще в её голову попадает.
    """
    y = df["y"].values
    order = np.argsort(-scores)
    out = {}
    for k in k_values:
        if len(order) < k:
            continue
        top = order[:k]
        is_fresh = fresh[top]
        out[f"топ-{k}"] = {
            "новый_отказ_строк": int(is_fresh.sum()),
            "новый_отказ_попаданий": int(y[top][is_fresh].sum()),
            "продолжение_строк": int((~is_fresh).sum()),
            "продолжение_попаданий": int(y[top][~is_fresh].sum()),
            "попаданий_всего": int(y[top].sum()),
        }
    return out


def queue_strata(df, scores, fresh, own_fault, sweep_only,
                 k_values=(50, 100, 500)):
    """Из какого страта набрана голова очереди."""
    y = df["y"].values
    order = np.argsort(-scores)
    out = {}
    for k in k_values:
        if len(order) < k:
            continue
        top = order[:k]
        out[f"топ-{k}"] = {
            name: {"строк": int(mask[top].sum()),
                   "попаданий": int(y[top][mask[top]].sum())}
            for name, mask in (("новый_отказ", fresh),
                               ("свой_отказ", own_fault),
                               ("только_массовый_день", sweep_only))
        }
    return out


def main():
    con = connect()
    forest = load_model()

    own_fault_window(con)

    cols = ", ".join(f"f.{c}" for c in FEATURE_NAMES)
    test = con.execute(f"""
        SELECT f.channel_id, f.d, f.y, {cols},
               COALESCE(o.own_fault_7d, 0) AS own_fault_7d
        FROM features f
        LEFT JOIN own_fault o
               ON o.channel_id = f.channel_id AND o.d = f.d
        WHERE f.d BETWEEN DATE '{model.TEST_FROM}' AND DATE '{model.TEST_TO}'
        ORDER BY f.channel_id, f.d
    """).df()

    X, _ = model.prepare(test)
    scores = model.predict(forest, X).astype("float32")

    fault = test["fault_ev_7d"].fillna(0).values
    own = test["own_fault_7d"].fillna(0).values
    fresh = fault == 0                      # не сообщал вовсе
    own_fault = own > 0                     # сообщал вне массовых суток
    sweep_only = (fault > 0) & (own == 0)   # сообщал, но только в массовые
    ongoing = ~fresh                        # прежний разрез, для сопоставимости

    y = test["y"].values
    report = {
        "измерено": {
            "отложенный_период": f"{model.TEST_FROM}..{model.TEST_TO}",
            "канало-суток": int(len(test)),
            "позитивов": int(y.sum()),
        },
        "разложение_позитивов": {
            "новый_отказ": {
                "позитивов": int(y[fresh].sum()),
                "доля": float(y[fresh].sum() / y.sum()) if y.sum() else 0.0,
            },
            "продолжение": {
                "позитивов": int(y[ongoing].sum()),
                "доля": float(y[ongoing].sum() / y.sum()) if y.sum() else 0.0,
            },
        },
        # Голова очереди по общему ранжированию: кто туда попадает.
        "состав_очереди": queue_composition(test, scores, fresh),
        "состав_очереди_по_стратам": queue_strata(test, scores, fresh,
                                                  own_fault, sweep_only),
        "популяции": {
            "новый_отказ": population_metrics(test, scores, fresh),
            "свой_отказ": population_metrics(test, scores, own_fault),
            "только_массовый_день": population_metrics(test, scores, sweep_only),
            "продолжение": population_metrics(test, scores, ongoing),
            "всё вместе": population_metrics(test, scores,
                                             np.ones(len(test), bool)),
        },
    }

    calib = os.path.join(config.MODEL_DIR, "sensor_calibration.json")
    if os.path.exists(calib):
        with open(calib, encoding="utf-8") as fh:
            report["оснащённость_точек_размеры_групп"] = \
                json.load(fh)["оснащённость_точек"]

    with open(TARGET, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    d = report["разложение_позитивов"]
    p = report["популяции"]
    print(f"отложенный период: {report['измерено']['канало-суток']} канало-суток, "
          f"{report['измерено']['позитивов']} позитивов")
    print(f"  новый отказ:  {d['новый_отказ']['позитивов']} "
          f"({d['новый_отказ']['доля']:.0%} позитивов), "
          f"P@50 {p['новый_отказ'].get('precision@50', 0):.0%}")
    print(f"  продолжение:  {d['продолжение']['позитивов']} "
          f"({d['продолжение']['доля']:.0%} позитивов), "
          f"P@50 {p['продолжение'].get('precision@50', 0):.0%}")
    print(f"  всё вместе:   P@50 {p['всё вместе'].get('precision@50', 0):.0%}")
    print("\nстраты:")
    for name in ("новый_отказ", "свой_отказ", "только_массовый_день"):
        s = p[name]
        print(f"  {name:22s} {s.get('канало-суток', 0):>9} канало-суток, "
              f"{s.get('позитивов', 0):>5} позитивов, "
              f"база {s.get('базовая_частота', 0):.4%}, "
              f"P@50 {s.get('precision@50', 0):.0%}")
    print("\nсостав головы очереди по стратам:")
    for key, row in report["состав_очереди_по_стратам"].items():
        parts = ", ".join(f"{n}: {v['строк']} строк/{v['попаданий']} попад."
                          for n, v in row.items())
        print(f"  {key}: {parts}")
    print("\nсостав головы очереди (общее ранжирование):")
    for key, row in report["состав_очереди"].items():
        print(f"  {key}: новых отказов {row['новый_отказ_строк']} строк "
              f"({row['новый_отказ_попаданий']} попаданий), "
              f"продолжений {row['продолжение_строк']} строк "
              f"({row['продолжение_попаданий']} попаданий)")
    print(f"\nсохранено: {TARGET}")
    con.close()


if __name__ == "__main__":
    main()
