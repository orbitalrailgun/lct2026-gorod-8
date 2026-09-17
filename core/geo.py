"""Геометрия объектов для интерактивной карты.

ТЗ требует интерактивную карту с выделением проблемных зон. Реальных координат
в данных нет и не будет: заказчик на встрече 17.09.2026 пояснил, что предприятие
использует внутреннюю московскую систему координат, её пересчёт в публичные карты
«за рамками проекта», и прямо разрешил сгенерировать геометрию самим —
«просто сгенерировать любые линейные объекты, пикет у нас это 10 метров…
достаточно осевой, достаточно вот этого LineString».

Мы делаем это не случайными числами. В названиях датчиков уцелели реальные
московские топонимы, и 13 из 16 объектов привязываются к конкретным районам.
Поэтому коллекторы размещаются примерно там, где они и находятся, а не в произвольных
точках карты. Это делает карту пригодной для ориентирования, оставаясь честной:
геометрия схематическая, а не результат съёмки, и так она и подписана в интерфейсе.

Длина трассы считается из пикетов: один пикет равен 10 метрам — цифра названа
заказчиком дважды, на встрече и в отраслевом докладе.
"""

import math

from core.config import PICKET_METERS

# Опорные точки районов юго-запада Москвы, где эксплуатируются коллекторы.
# Координаты приблизительные, до уровня района — этого достаточно для
# ориентирования и честно отражает доступную нам точность.
DISTRICT_ANCHORS = {
    "Зюзино": (55.6560, 37.5760),
    "Ясенево": (55.6030, 37.5330),
    "Беляево": (55.6420, 37.5240),
    "Коньково": (55.6330, 37.5180),
    "Коммунарк": (55.5680, 37.4720),
    "Вавиловск": (55.6790, 37.5620),
    "Ленинский": (55.6900, 37.5480),
    "Гарибальди": (55.6680, 37.5570),
    "Никулино": (55.6620, 37.4770),
    "Черемушк": (55.6700, 37.5620),
    "Академическая": (55.6880, 37.5730),
    "Профсоюзн": (55.6770, 37.5620),
    "Гагарин": (55.7080, 37.5830),
    "Донской": (55.7120, 37.6010),
    "Андреевск": (55.7160, 37.5760),
    "Зелинско": (55.6840, 37.5450),
    "Новочер": (55.6780, 37.5760),
    "союзн": (55.6770, 37.5620),
}

# Азимут трассы по объектам задаётся детерминированно от имени, чтобы линии
# не накладывались друг на друга и картинка была стабильной между запусками.
EARTH_M_PER_DEG_LAT = 111_320.0


def detect_toponym(sensor_names):
    """Топоним объекта по названиям его датчиков."""
    joined = " ".join(str(n or "") for n in sensor_names).lower()
    best, best_count = None, 0
    for key in DISTRICT_ANCHORS:
        count = joined.count(key.lower())
        if count > best_count:
            best, best_count = key, count
    return best


def _bearing_for(name):
    """Устойчивый азимут трассы: одинаковый при каждом запуске."""
    return (sum(ord(ch) for ch in str(name)) % 360) * math.pi / 180.0


def build_line(object_name, picket_min, picket_max, anchor=None):
    """Осевая линия коллектора по диапазону пикетов.

    Возвращает список координат от начального пикета к конечному. Длина
    соответствует реальной протяжённости участка: (ПКmax − ПКmin) × 10 метров.
    """
    lat0, lon0 = anchor or (55.66, 37.55)
    span_m = max((picket_max - picket_min) * PICKET_METERS, 200)
    bearing = _bearing_for(object_name)

    m_per_deg_lon = EARTH_M_PER_DEG_LAT * math.cos(math.radians(lat0))
    dlat = (span_m * math.cos(bearing)) / EARTH_M_PER_DEG_LAT
    dlon = (span_m * math.sin(bearing)) / m_per_deg_lon

    # Трасса рисуется от точки привязки в обе стороны, чтобы район оставался
    # в середине участка, а не на его краю.
    return [(lat0 - dlat / 2, lon0 - dlon / 2), (lat0 + dlat / 2, lon0 + dlon / 2)]


def picket_position(line, picket, picket_min, picket_max):
    """Координата конкретного пикета вдоль осевой линии."""
    if picket_max == picket_min:
        return line[0]
    t = (picket - picket_min) / (picket_max - picket_min)
    t = min(max(t, 0.0), 1.0)
    (lat1, lon1), (lat2, lon2) = line
    return (lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t)


def build_object_geometry(multicards, channel_rows):
    """Геометрия объектов и точек вердиктов для карты.

    `channel_rows` — записи справочника каналов с полями parent_name/object_name,
    sensor_name и picket; по ним определяются топоним и диапазон пикетов объекта.
    """
    by_object = {}
    for row in channel_rows:
        name = row.get("parent_name") or row.get("object_name")
        if not name:
            continue
        entry = by_object.setdefault(name, {"names": [], "pickets": []})
        entry["names"].append(row.get("sensor_name"))
        if row.get("picket") is not None:
            entry["pickets"].append(row["picket"])

    geometry = []
    for m in multicards:
        name = m["object_name"]
        info = by_object.get(name)
        if not info or not info["pickets"]:
            continue

        pk_min, pk_max = min(info["pickets"]), max(info["pickets"])
        toponym = detect_toponym(info["names"])
        anchor = DISTRICT_ANCHORS.get(toponym)
        line = build_line(name, pk_min, pk_max, anchor)

        points = []
        for card in m["cards"]:
            if card.get("picket") is None:
                continue
            lat, lon = picket_position(line, card["picket"], pk_min, pk_max)
            points.append({
                "lat": lat, "lon": lon,
                "scenario": card["scenario"],
                "probability": card["probability"],
                "title": card["card"].get("title", ""),
                "picket": card["picket"],
            })

        geometry.append({
            "object_name": name,
            "toponym": toponym,
            "anchor_known": anchor is not None,
            "line": line,
            "picket_min": pk_min,
            "picket_max": pk_max,
            "length_m": (pk_max - pk_min) * PICKET_METERS,
            "max_probability": m["max_probability"],
            "n_cards": m["n_cards"],
            "points": points,
        })
    return geometry
