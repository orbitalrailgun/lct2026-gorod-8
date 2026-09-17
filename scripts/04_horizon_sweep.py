"""День 2: качество модели в зависимости от горизонта прогноза.

ТЗ задаёт горизонт «не менее 24 часов» и Precision 0,7. Организаторы на встрече
17.09 разрешили менять целевые значения при письменном обосновании. Этот скрипт
и производит обоснование: показывает, что даёт каждый горизонт на этих данных.

Запуск:  .venv/bin/python -m scripts.04_horizon_sweep
"""

import json

from core import config, etl, features, labels, model


def run_for(con, hours):
    labels.build_labels(con, hours=hours)
    etl.load_mart(con, "dim_channel")
    features.build_features(con, "2022-01-01", "2026-06-30")
    train_df, test_df = model.load_split(con)
    X_tr, y_tr = model.prepare(train_df)
    X_te, y_te = model.prepare(test_df)
    models = model.train(X_tr, y_tr)
    scores = model.predict(models, X_te)
    m = model.evaluate(y_te, scores)
    thr = model.threshold_for_precision(y_te, scores, config.TARGET_PRECISION)
    m["target_precision_achieved"] = thr["achieved"]
    m["best_precision"] = thr.get("precision", thr.get("best_precision"))
    m["recall_at_best"] = thr.get("recall", thr.get("recall_at_best"))
    m["horizon_hours"] = hours
    return m


def main():
    con = etl.connect()
    for m in ("dim_channel", "fact_channel_day", "channel_profile", "logical_domain"):
        etl.load_mart(con, m)
    labels.build_sweep_days(con)

    rows = []
    for hours in (24, 72, 168, 336):
        r = run_for(con, hours)
        rows.append(r)
        d = hours // 24
        print(f"горизонт {d:>2} сут | позитивов {r['positives']:>6,} "
              f"| база {r['base_rate']:.4%} | PR-AUC {r['pr_auc']:.4f} "
              f"| P@100 {r['precision@100']:.1%} | lift@100 {r['lift@100']:>4.0f}x "
              f"| макс. precision {r['best_precision']:.0%}", flush=True)

    with open("models/horizon_sweep.json", "w") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print("\nсохранено в models/horizon_sweep.json")


if __name__ == "__main__":
    main()
