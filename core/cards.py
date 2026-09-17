"""Сборка карточек и мультикарточек.

Ключевое решение: единицей работы бригады является ОБЪЕКТ, а не канал.
Люди выезжают на объект и проверяют его целиком, поэтому вердикты по одному
объекту собираются в мультикарточку с общим таймлайном. Отдельная карточка
остаётся внутри как детализация, а не как самостоятельная задача.

Без этой группировки диспетчер получал бы семь строк про один и тот же объект
и не видел бы, что это одна поездка.
"""

import datetime as dt
from collections import Counter

from core import timeline

# Родительный падеж для фразы «отказ датчика ...» — заранее, чтобы не строить
# морфологию в рантайме и не зависеть от внешних библиотек.
SENSOR_GENITIVE = {
    "Датчик дыма": "дымового датчика",
    "Тепловой датчик": "теплового датчика",
    "Датчик температуры": "датчика температуры",
    "Газовый датчик": "газового датчика",
    "Датчик движения": "датчика движения",
    "Датчик затопления": "датчика затопления",
    "КД Дверь": "дверного контакта",
    "КД Люк": "контакта люка",
    "КД АВ": "контакта АВ",
    "Стекло": "датчика разбития стекла",
    "Ручной извещатель": "ручного извещателя",
    "Состояние насоса": "канала насоса",
    "Состояние вентилятора": "канала вентилятора",
    "Состояние фазы": "канала контроля фазы",
    "Переключатель": "переключателя",
    "Состояние УИР-Р": "канала УИР-Р",
    "ИБП": "источника бесперебойного питания",
}

SCENARIO_TITLE = {
    "отказ": "Прогноз отказа",
    "подтопление": "Аномальный режим откачки",
    "пожар": "Возможное задымление",
    "проникновение": "Возможное проникновение",
}

SCENARIO_PLURAL = {
    "отказ": ("отказ", "отказа", "отказов"),
    "подтопление": ("подтопление", "подтопления", "подтоплений"),
    "пожар": ("задымление", "задымления", "задымлений"),
    "проникновение": ("проникновение", "проникновения", "проникновений"),
}


def plural(n, forms):
    """Русское склонение числительного: 1 отказ, 2 отказа, 5 отказов."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def build_title(scenario, sensor_name=None, sensor_type=None,
                object_name=None, picket=None):
    """«Что произошло» человеческой фразой, а не кодом типа.

    Диспетчер должен понять суть из одной строки, не раскрывая карточку.
    """
    where = []
    if object_name:
        # Справочник содержит название вида «объект Гамма» или «ПС объект Ро».
        # Нужно «на объекте Гамма», поэтому слово склоняется, а не дублируется.
        name = str(object_name).strip()
        low = name.lower()
        if low.startswith("объект "):
            where.append("на объекте " + name[len("объект "):])
        elif " объект " in low:
            head, _, tail = name.partition(" объект ")
            where.append(f"на объекте {tail} ({head})")
        else:
            where.append(f"на объекте {name}")
    if picket is not None:
        where.append(f"ПК{int(picket)}")
    where_text = ", ".join(where)

    if scenario == "отказ":
        what = SENSOR_GENITIVE.get(sensor_type, "датчика")
        name = f" {sensor_name}" if sensor_name else ""
        return f"Прогноз отказа {what}{name} {where_text}".strip()

    if scenario == "подтопление":
        name = f" {sensor_name}" if sensor_name else ""
        return f"Аномальный режим откачки{name} {where_text}".strip()

    if scenario == "пожар":
        return f"Возможное задымление {where_text}".strip()

    if scenario == "проникновение":
        return f"Возможное проникновение {where_text}".strip()

    return f"{SCENARIO_TITLE.get(scenario, scenario)} {where_text}".strip()


def enrich_card(con, verdict, day):
    """Дополняет карточку заголовком, метками признаков и таймлайном."""
    card = verdict["card"]
    card["title"] = build_title(
        verdict["scenario"],
        sensor_name=card.get("sensor_name"),
        sensor_type=card.get("sensor_type"),
        object_name=verdict.get("object_name"),
        picket=verdict.get("picket"),
    )

    if verdict.get("channel_id"):
        series = timeline.load_channel_series(con, verdict["channel_id"], day)
        timeline.enrich_evidence(card["evidence"], series, _as_date(day))
    else:
        # У сценариев, работающих эпизодами (пожар, проникновение), метка
        # известна точно: это время самого эпизода.
        for item in card["evidence"]:
            item.setdefault("since", card.get("episode_at") or _as_date(day).isoformat())

    card["first_sign_at"] = timeline.first_sign_at(card["evidence"])
    card["incubation_days"] = timeline.incubation_days(card["evidence"], _as_date(day))
    card["timeline"] = timeline.build_timeline(card["evidence"])
    return verdict


def _as_date(day):
    return dt.date.fromisoformat(day) if isinstance(day, str) else day


def incubation_phrase(days):
    """Формулировка про длительность вызревания — она меняет тип реакции."""
    if days is None:
        return None
    if days <= 0:
        return "признаки появились сегодня — развитие внезапное"
    if days == 1:
        return "признаки копятся сутки"
    if days <= 3:
        return f"признаки копятся {days} суток"
    return (f"признаки копятся {days} суток — деградация постепенная, "
            "вероятно, можно закрыть плановой заявкой")


# ------------------------------------------------------- мультикарточка

def group_by_object(verdicts):
    """Собирает вердикты в мультикарточки по объектам.

    Порядок внутри — по убыванию риска, порядок мультикарточек — по максимальному
    риску на объекте. Бригада должна видеть сначала объект, куда ехать в первую
    очередь, и уже внутри — что именно там проверять.
    """
    groups = {}
    for v in verdicts:
        key = v.get("object_name") or "без привязки к объекту"
        groups.setdefault(key, []).append(v)

    multicards = []
    for name, items in groups.items():
        items.sort(key=lambda x: -x["probability"])
        multicards.append(build_multicard(name, items))
    multicards.sort(key=lambda m: -m["max_probability"])
    return multicards


def build_multicard(object_name, items):
    """Мультикарточка объекта: общее описание, общий таймлайн, вложенные карточки."""
    scenarios = Counter(v["scenario"] for v in items)
    pickets = sorted({v["picket"] for v in items if v.get("picket") is not None})

    merged = merge_timeline(items)

    firsts = [v["card"].get("first_sign_at") for v in items
              if v["card"].get("first_sign_at")]

    return {
        "object_name": object_name,
        "n_cards": len(items),
        "max_probability": max(v["probability"] for v in items),
        "scenarios": dict(scenarios),
        "pickets": pickets,
        "summary": build_summary(object_name, scenarios, pickets, items),
        "first_sign_at": min(firsts) if firsts else None,
        "timeline": merged,
        "cards": items,
    }


def merge_timeline(items):
    """Общий таймлайн объекта со схлопыванием одинаковых событий.

    Разные каналы одного объекта часто дают дословно совпадающие признаки
    в одни сутки. В общем ряду это читается как сбой, поэтому одинаковые
    записи объединяются с указанием, скольких каналов они касаются.
    """
    buckets = {}
    for v in items:
        loc = v.get("picket")
        for entry in v["card"].get("timeline", []):
            key = (entry["ts"][:10], entry["kind"], entry["text"])
            bucket = buckets.setdefault(key, {"ts": entry["ts"], "kind": entry["kind"],
                                              "text": entry["text"], "sources": []})
            label = f"ПК{loc}" if loc is not None else (v["card"].get("sensor_name") or "")
            if label and label not in bucket["sources"]:
                bucket["sources"].append(label)

    merged = []
    for bucket in buckets.values():
        n = len(bucket["sources"])
        if n > 1:
            shown = ", ".join(bucket["sources"][:3])
            more = f" и ещё {n - 3}" if n > 3 else ""
            bucket["text"] = f"{bucket['text']} — на {n} точках ({shown}{more})"
        elif n == 1:
            bucket["text"] = f"{bucket['text']} — {bucket['sources'][0]}"
        merged.append(bucket)
    merged.sort(key=lambda x: x["ts"])
    return merged


def build_summary(object_name, scenarios, pickets, items):
    """Одна фраза, по которой принимается решение о выезде."""
    parts = []
    for scenario, count in scenarios.most_common():
        forms = SCENARIO_PLURAL.get(scenario)
        word = plural(count, forms) if forms else scenario
        parts.append(f"{count} {word}")

    where = ""
    if pickets:
        if len(pickets) == 1:
            where = f", ПК{pickets[0]}"
        else:
            where = f", участок ПК{pickets[0]}–ПК{pickets[-1]}"

    top = max(items, key=lambda v: v["probability"])
    # заголовок не приводим к нижнему регистру: он содержит обозначения
    # приборов (МРСБ, АВ, ПК906), которые регистр делает нечитаемыми
    return (f"{object_name}{where}: {', '.join(parts)}. "
            f"Наибольший риск {top['probability']:.0%} — "
            f"{top['card'].get('title', '')}.")


def blind_spot_summary(items):
    """Сводка ограничений наблюдаемости по объекту.

    Если половина вердиктов объекта имеет слепые зоны, бригада должна знать
    это до выезда: часть проверок придётся делать глазами, а не по приборам.
    """
    with_blind = [v for v in items if v["card"].get("blind_spots")]
    if not with_blind:
        return None
    share = len(with_blind) / len(items)
    if share >= 0.5:
        return (f"У {len(with_blind)} из {len(items)} вердиктов ограничена "
                "наблюдаемость — подтвердить приборами получится не всё")
    return None
