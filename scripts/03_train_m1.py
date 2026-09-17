"""День 2: обучение M1 «отказ канала в ближайшие 24 часа».

Запуск:  .venv/bin/python -m scripts.03_train_m1
"""

import json
import os
import pickle
import time

import numpy as np

from core import config, etl, explain, model
from core.features import FEATURE_NAMES


def main():
    con = etl.connect()
    for m in ("dim_channel", "channel_profile", "features"):
        etl.load_mart(con, m)

    t0 = time.time()
    train_df, test_df = model.load_split(con)
    print(f"обучающая:  {len(train_df):>10,} строк, позитивов {int(train_df.y.sum()):>6,}")
    print(f"отложенная: {len(test_df):>10,} строк, позитивов {int(test_df.y.sum()):>6,}")
    print(f"период обучения {model.TRAIN_FROM}..{model.TRAIN_TO}, "
          f"проверки {model.TEST_FROM}..{model.TEST_TO}\n")

    X_train, y_train = model.prepare(train_df)
    X_test, y_test = model.prepare(test_df)

    models = model.train(X_train, y_train)
    print(f"обучено за {time.time() - t0:.0f} с\n")

    scores = model.predict(models, X_test)
    metrics = model.evaluate(y_test, scores)

    print("=== МЕТРИКИ НА ОТЛОЖЕННОЙ ВЫБОРКЕ ===")
    print(f"  канало-суток            {metrics['n']:>12,}")
    print(f"  позитивов               {metrics['positives']:>12,}")
    print(f"  базовая частота         {metrics['base_rate']:>12.4%}")
    print(f"  PR-AUC                  {metrics['pr_auc']:>12.4f}")
    print(f"  ROC-AUC                 {metrics['roc_auc']:>12.4f}")
    for k in (50, 100, 500):
        print(f"  precision@{k:<4}          {metrics[f'precision@{k}']:>12.1%}"
              f"   (в {metrics[f'lift@{k}']:.0f} раз выше случайного)")

    print("\n=== ЦЕЛЕВЫЕ ЗНАЧЕНИЯ ТЗ ===")
    thr = model.threshold_for_precision(y_test, scores, config.TARGET_PRECISION)
    if thr["achieved"]:
        print(f"  Precision {config.TARGET_PRECISION:.0%} достижима: "
              f"recall {thr['recall']:.1%} при пороге {thr['threshold']:.3f}")
    else:
        print(f"  Precision {config.TARGET_PRECISION:.0%} НЕ достижима. "
              f"Максимум {thr['best_precision']:.1%} при recall {thr['recall_at_best']:.1%}")

    print("\n=== ВАЖНОСТЬ ПРИЗНАКОВ ===")
    imp = model.feature_importance(models)
    for _, r in imp.head(10).iterrows():
        print(f"  {r['feature']:<24} {r['importance']:.4f}")

    print("\n=== ПРОВЕРКА ТОЧНОСТИ ОБЪЯСНЕНИЯ ===")
    sample = X_test[np.argsort(-scores)[:20]]
    ok, worst = explain.check_additivity(models["forest"], sample, tol=1e-6)
    print(f"  сумма вкладов равна предсказанию: {'да' if ok else 'НЕТ'}"
          f"   (максимальное расхождение {worst:.2e})")

    os.makedirs(config.MODEL_DIR, exist_ok=True)
    with open(os.path.join(config.MODEL_DIR, "m1_failure.pkl"), "wb") as fh:
        pickle.dump({"models": models, "features": FEATURE_NAMES}, fh)
    with open(os.path.join(config.MODEL_DIR, "m1_metrics.json"), "w") as fh:
        json.dump({"metrics": metrics, "target": thr}, fh, ensure_ascii=False, indent=1)
    print(f"\nмодель и метрики сохранены в {config.MODEL_DIR}")


if __name__ == "__main__":
    main()
