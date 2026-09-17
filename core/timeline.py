"""Временные метки признаков и построение таймлайна карточки.

Задача этого модуля — ответить на вопрос «когда это началось» для каждой улики.
Сложность в том, что улика обычно является агрегатом по окну: «78 сообщений
о неисправности за 7 суток» само по себе метки времени не имеет.

Принятое определение: **момент появления признака — это первые сутки внутри окна,
в которые условие стало истинным**. Для «молчит N суток» это день последней
активности плюс один, для «неисправность зафиксирована сегодня» — сама дата отказа.
Определение приблизительное по своей природе, поэтому оно проговаривается
в интерфейсе: диспетчер видит «признак появился», а не «событие произошло».

Зачем это нужно. Разница между «признаки копились три недели» и «всё возникло
за час» меняет решение: в первом случае это плановый ремонт, во втором —
аварийный выезд. Без меток времени вердикт этой разницы не передаёт.
"""

import datetime as dt

# Признаки, у которых момент появления не имеет смысла: это контекст,
# а не наблюдение. В таймлайн они не попадают.
CONTEXT_FEATURES = {"month", "is_weekend", "channel_age_days", "domain_size"}

KIND_SIGN = "признак"
KIND_CREATED = "вердикт"
KIND_ACK = "квитирование"
KIND_DECISION = "решение"


def _first_day_where(series, predicate):
    """Первые сутки в окне, где условие выполнено. Серия отсортирована по дате."""
    for row in series:
        if predicate(row):
            return row["d"]
    return None


def sign_since(feature, series, day):
    """Дата появления признака по суточной серии канала.

    `series` — список словарей по возрастанию даты с полями
    d, n_ev, n_fault, n_undefined, was_active.
    """
    if feature in CONTEXT_FEATURES:
        return None

    window7 = [r for r in series if (day - r["d"]).days < 7]
    window30 = [r for r in series if (day - r["d"]).days < 30]

    if feature in ("fault_ev_7d", "fault_ratio_90d", "dev_days_7d"):
        return _first_day_where(window7, lambda r: r["n_fault"] > 0)

    if feature == "fault_days_30d":
        return _first_day_where(window30, lambda r: r["n_fault"] > 0)

    if feature == "days_since_fault":
        # обратный порядок: нужна последняя неисправность, а не первая
        for row in reversed(series):
            if row["n_fault"] > 0:
                return row["d"]
        return None

    if feature in ("silence_days", "silence_ratio", "unknown_days_7d"):
        # Молчание начинается на следующие сутки после последней активности.
        # Но если канал выходил на связь сегодня, молчания нет вовсе, и метка
        # не должна уезжать в будущее — это выглядело бы как сбой системы.
        for row in reversed(series):
            if row["was_active"]:
                nxt = row["d"] + dt.timedelta(days=1)
                return min(nxt, day)
        return series[0]["d"] if series else None

    if feature in ("undefined_ev_7d", "undefined_share_7d"):
        return _first_day_where(window7, lambda r: r["n_undefined"] > 0)

    if feature in ("ev_7d", "ev_ratio_7d", "active_days_7d"):
        return _first_day_where(window7, lambda r: r["n_ev"] > 0)

    # признаки окружения: момент, когда в окне вообще появилась активность
    return _first_day_where(window7, lambda r: r["n_ev"] > 0 or r["n_fault"] > 0)


def load_channel_series(con, channel_id, day, days=90):
    """Суточная серия канала для расчёта меток. Один запрос на карточку."""
    rows = con.execute(f"""
        SELECT d, n_ev, n_fault + n_disabled AS n_fault, n_undefined, TRUE AS was_active
        FROM fact_channel_day
        WHERE channel_id = {channel_id}
          AND d BETWEEN DATE '{day}' - {days} AND DATE '{day}'
        ORDER BY d
    """).fetchall()
    return [{"d": r[0], "n_ev": r[1], "n_fault": r[2],
             "n_undefined": r[3], "was_active": True} for r in rows]


def enrich_evidence(evidence, series, day):
    """Проставляет каждой улике дату появления признака."""
    for item in evidence:
        since = sign_since(item["feature"], series, day)
        item["since"] = since.isoformat() if since else None
    return evidence


def build_timeline(evidence, created_at=None, acked_at=None,
                   decision=None, closed_at=None):
    """Хронология карточки: от появления первых признаков до закрытия.

    Признаки складываются в один ряд с действиями диспетчера, чтобы было видно
    главное — сколько времени между появлением признака и реакцией.
    """
    items = []
    for e in evidence:
        if e.get("since"):
            items.append({"ts": e["since"], "kind": KIND_SIGN, "text": e["phrase"]})

    if created_at:
        items.append({"ts": _iso(created_at), "kind": KIND_CREATED,
                      "text": "сформирован вердикт"})
    if acked_at:
        items.append({"ts": _iso(acked_at), "kind": KIND_ACK,
                      "text": "взят в работу диспетчером"})
    if closed_at:
        text = "закрыт"
        if decision:
            text = f"закрыт: {decision.get('action', '')}"
            if decision.get("reason"):
                text += f" ({decision['reason']})"
        items.append({"ts": _iso(closed_at), "kind": KIND_DECISION, "text": text})

    items.sort(key=lambda x: x["ts"])
    return items


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def first_sign_at(evidence):
    """Самая ранняя метка среди улик — «с какого момента это зреет»."""
    stamps = [e["since"] for e in evidence if e.get("since")]
    return min(stamps) if stamps else None


def incubation_days(evidence, day):
    """Сколько суток признаки копились до формирования вердикта.

    Продуктовый смысл: длинная инкубация означает деградацию, которую можно
    закрыть плановой заявкой; короткая — внезапный отказ, требующий выезда.
    """
    first = first_sign_at(evidence)
    if not first:
        return None
    if isinstance(day, str):
        day = dt.date.fromisoformat(day)
    return (day - dt.date.fromisoformat(first[:10])).days
