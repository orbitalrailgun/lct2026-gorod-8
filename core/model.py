"""Обучение и инференс M1 «отказ канала в ближайшие 24 часа».

Это единственная модель проекта с настоящей разметкой, поэтому именно здесь
предъявляются честные Precision и Recall. Для остальных сценариев метрики
считаются иначе, и это заявлено отдельно.

Два решения, которые стоит понимать при чтении кода:

* Отрицательные примеры прореживаются. Базовая частота 0,0413 % означает один
  позитив на 2 400 канало-суток; держать в памяти все 13,4 млн строк незачем,
  тем более на машине с 16 ГБ. Прореживание меняет абсолютную вероятность,
  но не меняет порядок — а диспетчеру нужна именно очередь по приоритету,
  поэтому метрики берём ранговые (PR-AUC, precision@k), а вероятность калибруем.

* Пропуск — это не ноль. Отношение к личной норме не определено, когда нормы ещё
  нет (канал слишком молод). Такое значение заполняется -1: для отношения это
  невозможная величина, поэтому дерево выделяет её в отдельную ветку и «нормы нет»
  остаётся самостоятельным состоянием, а не притворяется единицей.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.preprocessing import StandardScaler

from core.features import FEATURE_NAMES

MISSING = -1.0

TRAIN_FROM, TRAIN_TO = "2022-01-01", "2024-12-31"
TEST_FROM, TEST_TO = "2025-01-01", "2026-06-30"


def load_split(con, neg_per_pos=100, seed=42):
    """Обучающая и отложенная выборки, разделённые по времени.

    Сплит строго временной: обучаемся на 2022-2024, проверяем на 2025 и первой
    половине 2026. Случайное разбиение здесь недопустимо — соседние сутки одного
    канала почти одинаковы, и модель просто запомнила бы канал.

    Доля отрицательных подбирается от фактического числа позитивов, а не задаётся
    фиксированным делителем: при базовой частоте 0,04 % ошибка в делителе на
    порядок превращает выборку 1:100 в почти сбалансированную и ломает калибровку.
    """
    cols = ", ".join(FEATURE_NAMES)
    n_pos, n_neg = con.execute(f"""
        SELECT sum(y), count(*) - sum(y) FROM features
        WHERE d BETWEEN DATE '{TRAIN_FROM}' AND DATE '{TRAIN_TO}'
    """).fetchone()
    want_neg = min(int(n_pos) * neg_per_pos, int(n_neg))
    rate = want_neg / float(n_neg)

    # Порядок строк задаётся явно, и это не косметика. DuckDB читает
    # параллельно и порядок между прогонами не сохраняет, а лес берёт
    # бутстрап-выборки в порядке поступления строк — то есть одно и то же
    # обучение давало разные модели. Измерено: два прогона с одним и тем же
    # случайным зерном дали precision@100 0,16 и 0,09.
    train = con.execute(f"""
        SELECT channel_id, d, y, {cols} FROM features
        WHERE d BETWEEN DATE '{TRAIN_FROM}' AND DATE '{TRAIN_TO}'
          AND (y = 1 OR hash(channel_id * 100000 + epoch(d)::BIGINT) %% 1000000
                        < {int(rate * 1000000)})
        ORDER BY channel_id, d
    """.replace("%%", "%")).df()
    test = con.execute(f"""
        SELECT channel_id, d, y, {cols} FROM features
        WHERE d BETWEEN DATE '{TEST_FROM}' AND DATE '{TEST_TO}'
        ORDER BY channel_id, d
    """).df()
    return train, test


def prepare(df):
    """Матрица признаков без пропусков: NaN становится явным состоянием."""
    X = df[FEATURE_NAMES].astype(float).copy()
    X = X.replace([np.inf, -np.inf], np.nan).fillna(MISSING)
    return X.values, df["y"].values


def train(X, y, seed=42):
    """Два классификатора: лес для точности, логистическая регрессия для контроля.

    Лес даёт точные поканальные вклады через декомпозицию путей. Логистическая
    регрессия читается напрямую по коэффициентам и служит независимой проверкой:
    если топ-улики двух моделей расходятся, вердикт помечается ненадёжным.
    """
    forest = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=20,
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=seed,
    )
    forest.fit(X, y)

    scaler = StandardScaler().fit(X)
    linear = LogisticRegression(
        max_iter=2000, class_weight="balanced", random_state=seed
    )
    linear.fit(scaler.transform(X), y)
    return {"forest": forest, "linear": linear, "scaler": scaler}


def predict(models, X):
    """Вероятность отказа по лесу."""
    return models["forest"].predict_proba(X)[:, 1]


def evaluate(y_true, scores, k_values=(50, 100, 500)):
    """Метрики, осмысленные для очереди диспетчера.

    PR-AUC, а не ROC-AUC как главная: при доле позитивов 0,04 % ROC-AUC выглядит
    прекрасно даже у бесполезной модели. Precision@k отвечает на прямой вопрос
    эксплуатации: если бригада проверит топ-100 за сутки, сколько попаданий будет.
    """
    out = {
        "positives": int(np.sum(y_true)),
        "n": int(len(y_true)),
        "base_rate": float(np.mean(y_true)),
        "pr_auc": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)),
    }
    order = np.argsort(-scores)
    for k in k_values:
        top = order[:k]
        hits = int(np.sum(y_true[top]))
        out[f"precision@{k}"] = hits / k
        out[f"lift@{k}"] = (hits / k) / out["base_rate"] if out["base_rate"] else 0.0
    return out


def threshold_for_precision(y_true, scores, target_precision):
    """Порог, на котором достигается заданная точность.

    ТЗ задаёт Precision 0,7 и Recall 0,5 как целевые. Функция отвечает, при каком
    пороге точность достигается и какой ценой по полноте — это и есть материал
    для обоснования выбранных значений в пояснительной записке.
    """
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    ok = np.where(precision[:-1] >= target_precision)[0]
    if len(ok) == 0:
        best = int(np.argmax(precision[:-1]))
        return {
            "achieved": False,
            "best_precision": float(precision[best]),
            "recall_at_best": float(recall[best]),
            "threshold": float(thresholds[best]),
        }
    i = ok[int(np.argmax(recall[ok]))]
    return {
        "achieved": True,
        "precision": float(precision[i]),
        "recall": float(recall[i]),
        "threshold": float(thresholds[i]),
    }


def feature_importance(models):
    """Глобальная важность признаков леса — для документации, не для вердикта."""
    return pd.DataFrame(
        {"feature": FEATURE_NAMES, "importance": models["forest"].feature_importances_}
    ).sort_values("importance", ascending=False)
