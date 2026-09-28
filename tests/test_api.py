"""Тесты REST API и форматов обмена — раздел 7 ТЗ.

Браузер здесь не нужен: API отвечает обычным HTTP. Проверяется не только
код ответа, но и пригодность содержимого — XML должен разбираться,
CSV открываться, GeoJSON иметь правильный порядок координат. Эндпоинт,
отвечающий двести, но отдающий мусор, формально работает и практически
бесполезен.
"""

import csv
import io
import json
import xml.etree.ElementTree as ET

import pytest


def test_health_reports_database(api):
    status, body, _ = api("/api/health")
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["database"] is True, "стенд поднят без оперативного контура"


def test_verdicts_json_shape(api):
    status, body, headers = api("/api/verdicts?limit=3")
    assert status == 200
    payload = json.loads(body)
    assert payload["count"] == len(payload["items"]) <= 3
    item = payload["items"][0]
    for field in ("id", "scenario", "object_id", "object_name", "probability",
                  "horizon_hours", "status", "card"):
        assert field in item, f"в ответе нет поля {field}"


def test_verdicts_carry_full_card(api):
    """Карточка отдаётся целиком: без улик обмен теряет смысл."""
    _, body, _ = api("/api/verdicts?limit=1")
    card = json.loads(body)["items"][0]["card"]
    assert card.get("title")
    assert card.get("evidence"), "улики не переданы"
    assert "contribution" in card["evidence"][0]


def test_object_id_present_for_access_control(api):
    """По этому полю работает разграничение области видимости."""
    _, body, _ = api("/api/verdicts?limit=5")
    assert all(item["object_id"] is not None for item in json.loads(body)["items"])


@pytest.mark.parametrize("scenario", ["отказ", "пожар", "подтопление", "проникновение"])
def test_scenario_filter(api, scenario):
    from urllib.parse import quote
    status, body, _ = api(f"/api/verdicts?limit=50&scenario={quote(scenario)}")
    assert status == 200
    items = json.loads(body)["items"]
    assert items, f"нет вердиктов по сценарию {scenario}"
    assert all(i["scenario"] == scenario for i in items)


def test_xml_is_well_formed(api):
    status, body, headers = api("/api/verdicts.xml?limit=3")
    assert status == 200
    assert "xml" in headers.get("content-type", "").lower()
    root = ET.fromstring(body)
    assert root.tag == "verdicts"
    assert len(root) == int(root.get("count"))
    assert root[0].find("evidence") is not None


def test_csv_parses_and_has_bom(api):
    status, body, headers = api("/api/verdicts.csv?limit=5")
    assert status == 200
    assert "csv" in headers.get("content-type", "").lower()
    assert body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) >= 2
    assert rows[0][0] == "идентификатор"


def test_geojson_is_valid_collection(api):
    status, body, headers = api("/api/geometry.geojson")
    assert status == 200
    assert "geo+json" in headers.get("content-type", "").lower()
    collection = json.loads(body)
    assert collection["type"] == "FeatureCollection"
    assert collection["features"], "геометрия пуста"
    kinds = {f["properties"]["kind"] for f in collection["features"]}
    assert {"collector", "verdict"} <= kinds


def test_geojson_points_land_in_moscow(api):
    """Проверка порядка координат: перепутанный увозит объекты в океан."""
    _, body, _ = api("/api/geometry.geojson")
    points = [f for f in json.loads(body)["features"]
              if f["geometry"]["type"] == "Point"]
    assert points
    for feature in points:
        lon, lat = feature["geometry"]["coordinates"]
        assert 36 < lon < 39, f"долгота вне Москвы: {lon}"
        assert 54 < lat < 57, f"широта вне Москвы: {lat}"


def test_wkt_table(api):
    status, body, _ = api("/api/geometry.wkt")
    assert status == 200
    lines = body.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("wkt;")
    assert any(line.startswith("LINESTRING(") for line in lines)
    assert any(line.startswith("POINT(") for line in lines)


def test_xlsx_report_is_a_real_workbook(api):
    status, body, headers = api("/api/report.xlsx")
    assert status == 200
    assert body[:2] == b"PK", "это не XLSX"
    assert len(body) > 5000
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(body))
    assert set(book.sheetnames) >= {"Вердикты", "Сводка"}


def test_unknown_endpoint_is_not_silently_ok(api):
    status, _, _ = api("/api/nothing-here")
    assert status in (404, 405)


def test_verdict_texts_agree_in_numerals(api):
    """Согласование числительных проверяется на настоящих вердиктах."""
    import re
    _, body, _ = api("/api/verdicts?limit=200")
    text = body.decode("utf-8")
    for pattern, wrong in (
        (r"(?<!\d)1 сообщений", "«1 сообщений»"),
        (r"(?<!\d)1 суток", "«1 суток»"),
        (r"(?<!\d)1 каналов", "«1 каналов»"),
        (r"в [234] раз выше", "«в 2 раз выше»"),
        (r"(?<!\d)[234] признаков", "«4 признаков»"),
    ):
        assert not re.search(pattern, text), f"в вердиктах встречается {wrong}"
