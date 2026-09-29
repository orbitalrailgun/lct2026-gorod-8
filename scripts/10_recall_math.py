"""Цена полноты: сколько предупреждений в сутки требует заданный Recall.

Технического задания этот расчёт не требует, но без него разговор
о целевых метриках остаётся разговором. ТЗ называет Recall 0,5;
организаторы разрешили снижать показатели при письменном обосновании.
Обоснование и считается здесь: модель применяется ко всей отложенной
выборке, список сортируется по уверенности, и по нему определяется,
сколько позиций нужно взять для заданной полноты и какой точностью
это сопровождается.

Результат ложится в models/recall_math.json и используется диаграммой
«цена полноты» на странице аналитики и разделом 6.4 записки.

Память: выборка читается порциями по полмиллиона строк, оценки
хранятся во float32. На машине с 16 ГБ проходит с запасом.

Запуск:  python -m scripts.10_recall_math
"""

import json
import os
import pickle

import numpy as np

from core import config, etl, model
from core.features import FEATURE_NAMES

# Длина отложенного периода в сутках — нужна, чтобы перевести число
# предупреждений за весь период в нагрузку на одну смену.
TEST_DAYS = 547.0

CHUNK = 500_000
RECALL_POINTS = (0.10, 0.20, 0.30, 0.50, 0.70)
K_POINTS = (20, 50, 100, 200, 500, 1000)


def main():
    con = etl.connect()
    etl.load_mart(con, "features")

    with open(os.path.join(config.MODEL_DIR, "m1_failure.pkl"), "rb") as fh:
        forest = pickle.load(fh)["models"]["forest"]

    cols = ", ".join(FEATURE_NAMES)
    total = con.execute(
        f"SELECT count(*) FROM features "
        f"WHERE d BETWEEN DATE '{model.TEST_FROM}' AND DATE '{model.TEST_TO}'"
    ).fetchone()[0]
    print(f"строк в отложенной выборке: {total:,}".replace(",", " "), flush=True)

    scores, labels, offset = [], [], 0
    while offset < total:
        df = con.execute(f"""
            SELECT y, {cols} FROM features
            WHERE d BETWEEN DATE '{model.TEST_FROM}' AND DATE '{model.TEST_TO}'
            ORDER BY channel_id, d
            LIMIT {CHUNK} OFFSET {offset}
        """).df()
        if df.empty:
            break
        X = df[FEATURE_NAMES].astype("float32")
        X = X.replace([np.inf, -np.inf], np.nan).fillna(model.MISSING).values
        scores.append(forest.predict_proba(X)[:, 1].astype("float32"))
        labels.append(df["y"].values.astype("int8"))
        offset += CHUNK
        print(f"  обработано {min(offset, total):,}".replace(",", " "), flush=True)

    score = np.concatenate(scores)
    label = np.concatenate(labels)
    positives = int(label.sum())
    print(f"позитивов: {positives} из {len(label):,}".replace(",", " "))

    # Один проход по отсортированному списку: накопленная сумма меток
    # отвечает на оба вопроса — и «сколько поймали», и «какой ценой».
    caught = np.cumsum(label[np.argsort(-score)])

    payload = {"n": int(len(label)), "positives": positives, "days": TEST_DAYS,
               "recall_curve": [], "precision_at_k": []}

    print("\n=== ЦЕНА ПОЛНОТЫ ===")
    for recall in RECALL_POINTS:
        need = int(np.ceil(recall * positives))
        alerts = int(np.searchsorted(caught, need)) + 1
        payload["recall_curve"].append({
            "recall": recall, "alerts": alerts,
            "precision": need / alerts, "alerts_per_day": alerts / TEST_DAYS,
        })
        print(f"  recall {recall:.2f}  предупреждений {alerts:>7,}"
              f"  точность {need / alerts:.4f}"
              f"  в сутки {alerts / TEST_DAYS:>7.1f}".replace(",", " "))

    print("\n=== ТОЧНОСТЬ В ГОЛОВЕ ОЧЕРЕДИ ===")
    for k in K_POINTS:
        payload["precision_at_k"].append({
            "k": k, "precision": float(caught[k - 1]) / k,
            "recall": float(caught[k - 1]) / positives,
        })
        print(f"  k={k:<5} precision {float(caught[k - 1]) / k:.3f}"
              f"   recall {float(caught[k - 1]) / positives:.5f}")

    target = os.path.join(config.MODEL_DIR, "recall_math.json")
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"\nсохранено: {target}")


if __name__ == "__main__":
    main()
