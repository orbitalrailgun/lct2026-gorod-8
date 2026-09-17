"""Формирование вердиктов: от предсказания к карточке.

Здесь сходятся модель и слой объяснимости. На выходе не число, а документ,
который диспетчер может прочитать и проверить: что, почему, что это НЕ,
и чего мы не видим.

Последняя часть — не украшение. В 1 823 логических доменах из 3 759 всего один
канал, подтверждать показания нечем. Вердикт обязан это сказать, иначе отсутствие
подтверждения читается как его отсутствие по существу.
"""

import pickle

import datetime as dt

import numpy as np
import pandas as pd

from core import config, explain
from core.features import FEATURES, FEATURE_NAMES

MODEL_VERSION = "m1-failure-v1"


def load_model(path=None):
    path = path or f"{config.MODEL_DIR}/m1_failure.pkl"
    with open(path, "rb") as fh:
        return pickle.load(fh)["models"]


def score_day(con, models, day, limit=100, horizon_hours=None, with_precedent=True):
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
        v = _build(row, float(scores[i]), bias, evidence, models, X[i], horizon_hours)
        if with_precedent:
            v["card"]["precedent"] = find_precedent(
                con, v["object_id"], v["picket"], day)
        verdicts.append(v)
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


def _iso_moment(day_value, time_value):
    """Корректная ISO-метка из даты и времени.

    Дата приходит из pandas как Timestamp, а не date, поэтому прямая подстановка
    в строку давала «2026-05-06 00:00:00T10:46:30» — невалидную метку, которую
    интерфейс обрезал до полуночи. Собираем через datetime, а не через текст.
    """
    if day_value is None or time_value is None:
        return None
    day = pd.Timestamp(day_value).date()
    if isinstance(time_value, pd.Timedelta):
        time_value = (dt.datetime.min + time_value).time()
    elif isinstance(time_value, str):
        time_value = dt.time.fromisoformat(time_value)
    return dt.datetime.combine(day, time_value).isoformat()


def _episode_span(row):
    """Продолжительность эпизода словами — если она известна и содержательна."""
    start = _iso_moment(row.get("d"), row.get("first_t"))
    end = _iso_moment(row.get("d"), row.get("last_t"))
    if not start or not end or start == end:
        return start, None
    seconds = (dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)).total_seconds()
    if seconds < 60:
        span = f"{seconds:.0f} с"
    elif seconds < 3600:
        span = f"{seconds / 60:.0f} мин"
    else:
        span = f"{seconds / 3600:.1f} ч"
    return start, (f"эпизод длился {span}: "
                   f"с {start[11:19]} до {end[11:19]}")


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
        recommendation=recommend("отказ", row.get("sensor_type")),
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


# ------------------------------------------------- M2 «подтопление»

def score_pumps(con, day, limit=50):
    """Вердикты по аномалии откачки.

    Формулировка повторяет ту, которую произнёс заказчик: «столько-то пусков
    за сутки против нормы столько-то». Контекст сети отвечает на его же вопрос
    «откуда вода»: один горячий насос — локальная течь, десяток — водоприток.
    """
    from core import pumps

    df = con.execute(f"""
        SELECT a.*, p.t_first_start, p.t_last_start,
               c.sensor_name, c.picket, c.object_id, c.object_name,
               c.parent_name, n.n_hot, n.is_network_event
        FROM pump_anomaly a
        JOIN pump_day p ON p.channel_id = a.channel_id AND p.d = a.d
        JOIN dim_channel c ON c.channel_id = a.channel_id
        JOIN pump_network n ON n.d = a.d
        WHERE a.d = DATE '{day}' AND a.trit = 1
        ORDER BY a.robust_z DESC NULLS LAST
        LIMIT {limit}
    """).df()

    out = []
    for _, row in df.iterrows():
        first_start = _iso_moment(row.get("d"), row.get("t_first_start"))
        last_start = _iso_moment(row.get("d"), row.get("t_last_start"))
        evidence = [{
            "feature": "pump_starts",
            "group": "откачка",
            "value": float(row["n_starts"]),
            "contribution": 0.5,
            "since": first_start,
            "phrase": pumps.verdict_phrase(row["n_starts"], row["med_starts"],
                                           row["sensor_name"], row["picket"]),
        }]
        if first_start and last_start and first_start != last_start:
            evidence.append({
                "feature": "pump_window", "group": "откачка",
                "value": 0.0, "contribution": 0.0, "since": first_start,
                "phrase": (f"откачка шла с {first_start[11:19]} до {last_start[11:19]}"),
            })
        if row["ratio_to_norm"] and row["ratio_to_norm"] >= 1.5:
            evidence.append({
                "feature": "ratio", "group": "откачка", "since": first_start,
                "value": float(row["ratio_to_norm"]), "contribution": 0.3,
                "phrase": f"это в {row['ratio_to_norm']:.0f} раз выше личной нормы насоса",
            })
        if row["duty_seconds"] and row["duty_seconds"] > 0:
            evidence.append({
                "feature": "duty", "group": "откачка", "since": first_start,
                "value": float(row["duty_seconds"]), "contribution": 0.2,
                "phrase": f"наработка за сутки {row['duty_seconds'] / 60:.0f} минут",
            })

        if row["is_network_event"]:
            negatives = ["локальная течь: аномалия видна сразу на "
                         f"{int(row['n_hot'])} насосах сети — это общий водоприток"]
            blind = ["источник воды может быть выше по трассе: "
                     "уклон коллектора в данных не задан"]
        else:
            negatives = [f"общесетевой водоприток: кроме этого насоса аномальны "
                         f"лишь {int(row['n_hot']) - 1}"]
            blind = ["датчики затопления в сети не дают сигнала — "
                     "вода детектируется косвенно, по режиму откачки"]

        # Вероятность ведём от кратности к личной норме и абсолютного объёма,
        # а не от z-score: последний при малом разбросе даёт 99 % на пустом месте.
        ratio = float(row["ratio_to_norm"] or 1.0)
        excess = float(row["n_starts"]) - float(row["med_starts"] or 0)
        prob = min(0.95, 0.25 + 0.12 * min(ratio, 6.0) + 0.004 * min(excess, 50.0))
        card = explain.build_card(
            verdict_type="аномальный режим откачки",
            location=_location(row), probability=prob, bias=0.0,
            evidence=evidence, negatives=negatives, observability=blind,
            counterfactual_text=None,
            recommendation=recommend("подтопление"),
        )
        card["sensor_name"] = row["sensor_name"]
        card["sensor_type"] = "Состояние насоса"
        card["episode_at"] = first_start
        out.append({
            "scenario": "подтопление",
            "object_id": None if _isnan(row.get("object_id")) else int(row["object_id"]),
            "object_name": row.get("parent_name") or row.get("object_name"),
            "picket": None if _isnan(row.get("picket")) else int(row["picket"]),
            "channel_id": int(row["channel_id"]),
            "probability": prob, "horizon_hours": 24,
            "model_version": "m2-pump-v1", "card": card,
        })
    return out


# ------------------------------------------------------ M3 «пожар»

def score_fire(con, day, limit=20):
    """Вердикты по задымлению.

    Скор правиловый и это заявлено прямо: обучать классификатор не на чем.
    Зато подтверждение выражено тритом, и «подтвердить нечем» не подменяется
    на «всё в порядке» — именно так обстоит дело в 86 % случаев.
    """
    from core import fire

    df = con.execute(f"""
        SELECT * FROM fire_candidate WHERE d = DATE '{day}'
    """).df()
    if df.empty:
        return []

    out = []
    for _, row in df.iterrows():
        score, why = fire.score_candidate(row)
        if score <= 0:
            continue
        evidence = [{"feature": "fire", "group": "задымление",
                     "value": float(row["n_smoke_ch"]), "contribution": score / len(why),
                     "phrase": w} for w in why]
        obj = con.execute(
            f"SELECT any_value(parent_name) p, any_value(object_name) o "
            f"FROM dim_channel WHERE object_id = {int(row['object_id'])}").fetchone()
        loc = f"{obj[0] or obj[1]}, ПК{int(row['picket'])}"

        card = explain.build_card(
            verdict_type="возможное задымление",
            location=loc, probability=score, bias=0.0, evidence=evidence,
            negatives=["регламентная проверка: сработала лишь часть датчиков контроллера"]
                      if not row["is_sweep"] else [],
            observability=fire.blind_spot_note(row),
            counterfactual_text=None,
            recommendation=recommend("пожар"),
        )
        card["sensor_name"] = row["sample_name"]
        card["sensor_type"] = "Датчик дыма"
        start, span = _episode_span(row)
        card["episode_at"] = start
        if span:
            evidence.append({"feature": "episode_span", "group": "задымление",
                             "value": 0.0, "contribution": 0.0, "phrase": span,
                             "since": start})
        out.append({
            "scenario": "пожар",
            "object_id": int(row["object_id"]),
            "object_name": obj[0] or obj[1],
            "picket": int(row["picket"]),
            "channel_id": None,
            "probability": float(score), "horizon_hours": 24,
            "model_version": "m3-fire-v1", "card": card,
        })
    out.sort(key=lambda r: -r["probability"])
    return out[:limit]


# ----------------------------------------------- M4 «проникновение»

def score_intrusion_day(con, day, limit=20):
    """Вердикты по охранному контуру.

    Правила, а не модель: разметки проникновений нет и не будет. Приоритет
    сценария заказчик понизил сам, поэтому здесь минимальная достаточная логика —
    режим охраны, кратность по типам датчиков и время суток.
    """
    from core import intrusion

    df = con.execute(f"""
        SELECT * FROM intrusion_event WHERE d = DATE '{day}'
    """).df()
    if df.empty:
        return []

    out = []
    for _, row in df.iterrows():
        score, why = intrusion.score_intrusion(row)
        if score < 0.3:
            continue
        evidence = [{"feature": "intrusion", "group": "охрана",
                     "value": float(row["n_types"]), "contribution": score / max(len(why), 1),
                     "phrase": w} for w in why]
        loc = f"{row['parent_name'] or row['object_name']}, ПК{int(row['picket'])}"
        card = explain.build_card(
            verdict_type="возможное проникновение",
            location=loc, probability=float(score), bias=0.0, evidence=evidence,
            negatives=["плановые работы: объект в этот момент был под охраной"]
                      if int(row["armed_trit"]) == 1 else [],
            observability=intrusion.blind_spot_note(row),
            counterfactual_text=None,
            recommendation=recommend("проникновение"),
        )
        card["sensor_name"] = row["sample_name"]
        card["sensor_type"] = "Охранный контур"
        start, span = _episode_span(row)
        card["episode_at"] = start
        if span:
            evidence.append({"feature": "episode_span", "group": "охрана",
                             "value": 0.0, "contribution": 0.0, "phrase": span,
                             "since": start})
        out.append({
            "scenario": "проникновение",
            "object_id": int(row["object_id"]),
            "object_name": row["parent_name"] or row["object_name"],
            "picket": int(row["picket"]),
            "channel_id": None,
            "probability": float(score), "horizon_hours": 24,
            "model_version": "m4-intrusion-v1", "card": card,
        })
    out.sort(key=lambda r: -r["probability"])
    return out[:limit]


# ------------------------------------------- рекомендации и прецеденты

# Что делать — четвёртый вопрос карточки. Первые три отвечают «что», «почему»
# и «чего мы не видим», но бригаде на объекте нужен ответ на «что с этим делать».
# Тексты заведены таблицей, чтобы их правила команда, а не разработчик.
ACTIONS = {
    ("отказ", "Датчик дыма"): "Проверить шлейф пожарной сигнализации на участке и питание шкафа ОПС.",
    ("отказ", "Датчик температуры"): "Проверить линию связи датчика; при повторе — заменить измерительный элемент.",
    ("отказ", "Газовый датчик"): "Проверить питание и калибровку газоанализатора.",
    ("отказ", "КД Дверь"): "Проверить контактную группу и шлейф двери.",
    ("отказ", "Датчик движения"): "Проверить питание извещателя и юстировку.",
    ("отказ", None): "Проверить питание и линию связи канала.",
    ("подтопление", None): "Осмотреть приямок и решётки, проверить работу насоса и уровень притока. "
                           "При общесетевом водопритоке локальный ремонт не даст эффекта.",
    ("пожар", None): "Направить бригаду для визуального осмотра участка. "
                     "При отсутствии термоконтроля полагаться только на осмотр.",
    ("проникновение", None): "Проверить целостность люков и дверей на участке, "
                             "при подтверждении — вызвать группу реагирования.",
}


def recommend(scenario, sensor_type=None):
    """Рекомендация по сценарию и типу оборудования."""
    return (ACTIONS.get((scenario, sensor_type))
            or ACTIONS.get((scenario, None))
            or "Проверить состояние оборудования на участке.")


def find_precedent(con, object_id, picket, before_day, scenario="отказ"):
    """Похожий случай из истории — опора для решения диспетчера.

    Заказчик описывает карточку инцидента как документ с историей. Прецедент
    отвечает на вопрос «чем это кончилось в прошлый раз» и превращает вердикт
    из абстрактной оценки в знакомую ситуацию.
    """
    if object_id is None or picket is None:
        return None
    row = con.execute(f"""
        SELECT f.d, sum(f.n_fault + f.n_disabled) AS n_fault
        FROM fact_channel_day f
        JOIN dim_channel c ON c.channel_id = f.channel_id
        WHERE c.object_id = {object_id} AND c.picket = {picket}
          AND f.d < DATE '{before_day}' AND (f.n_fault + f.n_disabled) > 0
        GROUP BY f.d ORDER BY f.d DESC LIMIT 1
    """).fetchone()
    if not row:
        return None
    return f"Похожий случай: {row[0]:%d.%m.%Y}, та же точка, {int(row[1])} сообщений о неисправности."
