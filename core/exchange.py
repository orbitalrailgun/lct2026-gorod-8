"""Форматы обмена данными: XML, CSV, GeoJSON, WKT.

Раздел 7 ТЗ перечисляет форматы, в которых сервис обязан отдавать данные
наружу: JSON и XML для программного обмена, CSV и XLSX для файлового,
GeoJSON и WKT для геоданных. JSON и XLSX закрыты основным API и отчётом,
остальные три собираются здесь.

Модуль намеренно ничего не знает про HTTP: на вход — те же структуры,
что лежат в базе и уходят в JSON, на выход — строка или байты. Поэтому
одни и те же вердикты гарантированно одинаковы во всех форматах,
а не собираются заново под каждый.

Имена полей во всех форматах латинские и совпадают с ключами JSON.
Кириллица в именах XML-элементов допустима стандартом, но интегратор
обычно получает её в виде неудобных для XPath сущностей, а главное —
расхождение имён между JSON и XML заставило бы писать две разные
обвязки вместо одной.
"""

import csv
import io
import xml.etree.ElementTree as ET

# Поля вердикта в плоской выгрузке. Порядок значим: он же порядок колонок.
FLAT_FIELDS = [
    ("id", "идентификатор"),
    ("created_at", "сформирован"),
    ("scenario", "сценарий"),
    ("object_name", "объект"),
    ("picket", "пикет"),
    ("channel_id", "канал"),
    ("probability", "вероятность"),
    ("horizon_hours", "горизонт_часов"),
    ("status", "статус"),
    ("title", "описание"),
    ("first_sign_at", "первый_признак"),
    ("incubation_days", "вызревание_суток"),
    ("evidence", "улики"),
    ("blind_spots", "слепые_зоны"),
    ("recommendation", "рекомендация"),
]


def _flat(row):
    """Один вердикт как плоский словарь: карточка разворачивается в строку."""
    card = row.get("card") or {}
    created = row.get("created_at")
    return {
        "id": row.get("id"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else (created or ""),
        "scenario": row.get("scenario") or "",
        "object_name": row.get("object_name") or "",
        "picket": row.get("picket") if row.get("picket") is not None else "",
        "channel_id": row.get("channel_id") or "",
        "probability": f"{row.get('probability', 0):.4f}",
        "horizon_hours": row.get("horizon_hours") or "",
        "status": row.get("status") or "",
        "title": card.get("title") or "",
        "first_sign_at": card.get("first_sign_at") or "",
        "incubation_days": card.get("incubation_days")
                           if card.get("incubation_days") is not None else "",
        "evidence": "; ".join(e["phrase"] for e in card.get("evidence", [])),
        "blind_spots": "; ".join(card.get("blind_spots", [])),
        "recommendation": card.get("recommendation") or "",
    }


def to_csv(rows):
    """Плоская выгрузка вердиктов.

    Разделитель — точка с запятой, кодировка с меткой порядка байтов.
    Это не каприз: русская локаль Excel открывает запятую как десятичный
    разделитель и склеивает всю строку в одну ячейку, а без метки BOM
    показывает кириллицу знаками вопроса. Файл, который нельзя открыть
    двойным щелчком, бесполезен, каким бы правильным он ни был.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[f[0] for f in FLAT_FIELDS],
                            delimiter=";", quoting=csv.QUOTE_MINIMAL,
                            lineterminator="\r\n", extrasaction="ignore")
    buf.write("﻿")
    writer.writerow({key: title for key, title in FLAT_FIELDS})
    for row in rows:
        writer.writerow(_flat(row))
    return buf.getvalue().encode("utf-8")


def to_xml(rows):
    """Вердикты в XML со структурой карточки, а не плоские.

    Плоский XML был бы копией CSV и потерял бы главное — улики с вкладами.
    Программный обмен для того и нужен, чтобы принимающая система получила
    обоснование целиком, а не строку текста.
    """
    root = ET.Element("verdicts", {"count": str(len(rows))})
    for row in rows:
        card = row.get("card") or {}
        flat = _flat(row)
        item = ET.SubElement(root, "verdict", {"id": str(flat["id"])})
        for key in ("created_at", "scenario", "object_name", "picket",
                    "channel_id", "probability", "horizon_hours", "status"):
            ET.SubElement(item, key).text = str(flat[key])
        for key in ("title", "first_sign_at", "incubation_days", "recommendation"):
            if flat[key] != "":
                ET.SubElement(item, key).text = str(flat[key])

        evidence = card.get("evidence") or []
        if evidence:
            block = ET.SubElement(item, "evidence")
            for e in evidence:
                node = ET.SubElement(block, "item", {
                    "feature": str(e.get("feature", "")),
                    "contribution": f"{e.get('contribution', 0):.6f}",
                })
                node.text = e.get("phrase", "")
                if e.get("since"):
                    node.set("since", str(e["since"]))

        spots = card.get("blind_spots") or []
        if spots:
            block = ET.SubElement(item, "blind_spots")
            for text in spots:
                ET.SubElement(block, "item").text = text

        timeline = card.get("timeline") or []
        if timeline:
            block = ET.SubElement(item, "timeline")
            for mark in timeline:
                node = ET.SubElement(block, "mark", {
                    "at": str(mark.get("ts", "")),
                    "kind": str(mark.get("kind", "")),
                })
                node.text = mark.get("text", "")

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def to_geojson(geometry):
    """Геометрия объектов и точки вердиктов как FeatureCollection.

    Координаты в GeoJSON идут в порядке «долгота, широта» — обратном тому,
    как их привычно пишут и как они хранятся у нас. Перестановка здесь
    единственная и намеренная: иначе Москва уезжает в Индийский океан,
    а ошибка выглядит как проблема данных, а не формата.

    Система координат — WGS 84, как требует спецификация GeoJSON.
    Геометрия схематическая: реальных координат в исходных данных нет,
    трассы привязаны к районам по топонимам в названиях датчиков.
    """
    features = []
    for obj in geometry:
        line = obj.get("line") or []
        if len(line) >= 2:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lat, lon in line],
                },
                "properties": {
                    "kind": "collector",
                    "object_name": obj.get("object_name"),
                    "district": obj.get("toponym"),
                    "anchored": bool(obj.get("anchor_known")),
                    "picket_min": obj.get("picket_min"),
                    "picket_max": obj.get("picket_max"),
                    "length_m": obj.get("length_m"),
                    "verdicts": obj.get("n_cards"),
                    "max_probability": obj.get("max_probability"),
                },
            })
        for point in obj.get("points") or []:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [point["lon"], point["lat"]],
                },
                "properties": {
                    "kind": "verdict",
                    "object_name": obj.get("object_name"),
                    "picket": point.get("picket"),
                    "scenario": point.get("scenario"),
                    "probability": point.get("probability"),
                    "title": point.get("title"),
                },
            })
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }


def to_wkt(geometry):
    """Та же геометрия в Well-Known Text.

    Возвращается таблица с колонкой WKT, а не голый список геометрий:
    в таком виде файл открывается настольными ГИС напрямую, а имя объекта
    и сценарий не теряются по дороге.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    buf.write("﻿")
    writer.writerow(["wkt", "kind", "object_name", "picket", "scenario",
                     "probability"])
    for obj in geometry:
        line = obj.get("line") or []
        if len(line) >= 2:
            coords = ", ".join(f"{lon:.6f} {lat:.6f}" for lat, lon in line)
            writer.writerow([f"LINESTRING({coords})", "collector",
                             obj.get("object_name"), "", "",
                             f"{obj.get('max_probability', 0):.4f}"])
        for point in obj.get("points") or []:
            writer.writerow([f"POINT({point['lon']:.6f} {point['lat']:.6f})",
                             "verdict", obj.get("object_name"),
                             point.get("picket"), point.get("scenario"),
                             f"{point.get('probability', 0):.4f}"])
    return buf.getvalue().encode("utf-8")
