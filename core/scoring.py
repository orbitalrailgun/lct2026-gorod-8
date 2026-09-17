"""Формирование вердиктов: от предсказания к карточке.

Здесь сходятся модель и слой объяснимости. На выходе не число, а документ,
который диспетчер может прочитать и проверить: что, почему, что это НЕ,
и чего мы не видим.

Последняя часть — не украшение. В 1 823 логических доменах из 3 759 всего один
канал, подтверждать показания нечем. Вердикт обязан это сказать, иначе отсутствие
подтверждения читается как его отсутствие по существу.
"""

import pickle

import numpy as np
import pandas as pd

from core import config, explain
from core.features import FEATURES, FEATURE_NAMES

MODEL_VERSION = "m1-failure-v1"


def load_model(path=None):
    path = path or f"{config.MODEL_DIR}/m1_failure.pkl"
    with open(path, "rb") as fh:
        return pickle.load(fh)["models"]


def score_day(con, models, day, limit=100, horizon_hours=None):
    """Считает вердикты на конкретные сутки и возвращает готовые карточки."""
    horizon_hours = horizon_hours or config.HORIZON_HOURS
    cols = ", ".join(FEATURE_NAMES)
    df = con.execute(f"""
        SELECT f.channel_id, f.d, {cols},
               c.sensor_name, c.sensor_type, c.system, c.picket,
               c.object_id, c.object_name, c.parent_name,
               COALESCE(g.n_channels, 1) AS dom_size,
               COALESCE(g.has_thermal, FALSE) AS dom_thermal
        FROM features f
        JOIN dim_channel c ON c.channel_id = f.channel_id
        LEFT JOIN logical_domain g
               ON g.object_id = c.object_id AND g.picket = c.picket
        WHERE f.d = DATE '{day}'
    """).df()
    if df.empty:
        return []

    X = df[FEATURE_NAMES].astype(float).replace([np.inf, -np.inf], np.nan).fillna(-1.0).values
    scores = models["forest"].predict_proba(X)[:, 1]
    top = np.argsort(-scores)[:limit]

    verdicts = []
    for i in top:
        row = df.iloc[i]
        values = {name: row[name] for name in FEATURE_NAMES}
        bias, evidence = explain.top_evidence(models["forest"], X[i], values, k=4)
        verdicts.append(_build(row, float(scores[i]), bias, evidence,
                               models, X[i], horizon_hours))
    return verdicts


def _location(row, show_toponyms=False):
    """Подпись места.

    Локация официальная: ид_объект появился в обновлении датасета 16.09.2026.
    До пикета детализируем там, где он извлекается из названия датчика.
    Показ реальных топонимов вынесен в настройку: организаторы восстановили их
    в справочнике, но решение об их отображении остаётся за командой.
    """
    parts = []
    if row.get("parent_name"):
        parts.append(str(row["parent_name"]))
    elif row.get("object_name"):
        parts.append(str(row["object_name"]))
    if row.get("picket") is not None and not _isnan(row.get("picket")):
        parts.append(f"ПК{int(row['picket'])}")
    if not parts:
        parts.append(f"канал {int(row['channel_id'])}")
    return ", ".join(parts)


def _isnan(v):
    """Пропуск в любом виде: None, numpy.nan или pandas.NA из nullable-колонок."""
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return v is None


def _negatives(row):
    """Явное отрицание похожих гипотез.

    Диспетчер, увидев тревогу, сначала думает о худшем. Вердикт должен сразу
    снять те гипотезы, которые данные не подтверждают, — иначе бригада поедет
    искать пожар там, где отказал шлейф.
    """
    out = []
    system = str(row.get("system") or "")
    if "Пожарная" in system:
        out.append("пожар: сработок дыма за сутки не зафиксировано")
    if float(row.get("domain_dev_share_7d") or 0) < 0.2:
        out.append("массовая авария: соседние каналы точки в норме")
    return out


def _build(row, probability, bias, evidence, models, x, horizon_hours):
    """Собирает карточку вердикта целиком."""
    dom_size = row.get("dom_size")
    blind = explain.observability_note(
        domain_size=dom_size,
        domain_silent_share=row.get("domain_silent_share"),
        has_thermal=bool(row.get("dom_thermal")) if "Пожарная" in str(row.get("system") or "") else None,
    )

    # Контрфакт по сильнейшей улике: показывает, что именно чинить.
    cf_text = None
    if evidence:
        name = evidence[0]["feature"]
        idx = FEATURE_NAMES.index(name)
        normal = 0.0 if FEATURES[name]["higher_is_worse"] else float(x[idx])
        before, after = explain.counterfactual(models["forest"], x, idx, normal)
        if after < before - 0.01:
            cf_text = (f"Если устранить главную причину — {evidence[0]['phrase'].lower()} — "
                       f"риск снизится с {before:.0%} до {after:.0%}.")

    card = explain.build_card(
        verdict_type="отказ датчика",
        location=_location(row),
        probability=probability,
        bias=float(bias),
        evidence=evidence,
        negatives=_negatives(row),
        observability=blind,
        counterfactual_text=cf_text,
    )
    card["sensor_name"] = row.get("sensor_name")
    card["sensor_type"] = row.get("sensor_type")
    card["system"] = row.get("system")

    return {
        "scenario": "отказ",
        "object_id": None if _isnan(row.get("object_id")) else int(row["object_id"]),
        "object_name": row.get("parent_name") or row.get("object_name"),
        "picket": None if _isnan(row.get("picket")) else int(row["picket"]),
        "channel_id": int(row["channel_id"]),
        "probability": probability,
        "horizon_hours": horizon_hours,
        "model_version": MODEL_VERSION,
        "card": card,
    }
