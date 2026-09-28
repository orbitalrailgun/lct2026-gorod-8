"""Тесты логики, не требующие ни стенда, ни браузера.

Здесь проверяется то, на чём держатся выводы сервиса: согласование
числительных в карточках, троичная алгебра, семантический контракт
значений, устройство диаграмм и форматы обмена. Ошибка в любом из этих
мест портит все вердикты сразу, поэтому проверки дешёвые и подробные.
"""

import csv
import io
import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from core import cards, charts, contract, exchange, features, orders, plural, trits


# --------------------------------------------------- согласование числительных

@pytest.mark.parametrize("number,expected", [
    (0, "сообщений"), (1, "сообщение"), (2, "сообщения"), (4, "сообщения"),
    (5, "сообщений"), (11, "сообщений"), (12, "сообщений"), (14, "сообщений"),
    (21, "сообщение"), (22, "сообщения"), (25, "сообщений"),
    (101, "сообщение"), (111, "сообщений"), (1002, "сообщения"),
])
def test_plural_form(number, expected):
    assert plural.form(number, "сообщение", "сообщения", "сообщений") == expected


def test_plural_count_joins_number_and_form():
    assert plural.count(1, "пуск", "пуска", "пусков") == "1 пуск"
    assert plural.count(3, "пуск", "пуска", "пусков") == "3 пуска"
    assert plural.count(15, "пуск", "пуска", "пусков") == "15 пусков"


def test_plural_apply_substitutes_in_template():
    template = "канал выходил на связь {v:.0f} {сутки|суток|суток} из 7"
    assert plural.apply(template, 1).startswith("канал выходил на связь {v:.0f} сутки")
    assert "суток" in plural.apply(template, 3)


def test_plural_apply_does_not_touch_format_fields():
    """Обычные поля формата не должны пострадать от подстановки форм."""
    template = "событий за 7 суток: {v:.0f} при обычных {base:.0f}"
    assert plural.apply(template, 5) == template


@pytest.mark.parametrize("name,value,expected", [
    ("silence_days", 1, "молчит 1 сутки подряд"),
    ("silence_days", 3, "молчит 3 суток подряд"),
    ("days_since_fault", 1, "последняя неисправность: 1 сутки назад"),
    ("domain_size", 1, "в этой точке 1 канал"),
    ("domain_size", 3, "в этой точке 3 канала"),
    ("domain_size", 7, "в этой точке 7 каналов"),
    ("channel_age_days", 21, "канал в эксплуатации 21 сутки"),
])
def test_feature_phrases_agree(name, value, expected):
    assert features.describe(name, value) == expected


def test_no_unagreed_numerals_in_incubation_phrase():
    """Проверка по границе слова: «11 суток» верно, «1 суток» — нет."""
    import re
    for days in range(0, 70):
        phrase = cards.incubation_phrase(days) or ""
        assert not re.search(r"(?<!\d)1 суток", phrase), phrase
        assert not re.search(r"(?<!\d)[234] сутки", phrase), phrase


# ------------------------------------------------------------ троичная логика

def test_confirm_is_minimum():
    """Подтверждение: сосед в норме гасит одиночную тревогу."""
    assert trits.confirm(trits.DEVIATION, trits.NORMAL) == trits.NORMAL
    assert trits.confirm(trits.DEVIATION, trits.DEVIATION) == trits.DEVIATION


def test_unknown_is_preserved_not_rounded_to_normal():
    """Главное свойство троичной логики: «не знаю» не превращается в «нет»."""
    assert trits.confirm(trits.DEVIATION, trits.UNKNOWN) == trits.UNKNOWN
    assert trits.confirm(trits.UNKNOWN, trits.NORMAL) == trits.NORMAL


def test_escalate_is_maximum():
    assert trits.escalate(trits.NORMAL, trits.DEVIATION) == trits.DEVIATION
    assert trits.escalate(trits.UNKNOWN, trits.NORMAL) == trits.UNKNOWN


def test_negate_keeps_unknown():
    assert trits.negate(trits.DEVIATION) == trits.NORMAL
    assert trits.negate(trits.UNKNOWN) == trits.UNKNOWN


def test_trit_operations_work_on_arrays():
    left = np.array([trits.DEVIATION, trits.DEVIATION, trits.UNKNOWN])
    right = np.array([trits.NORMAL, trits.UNKNOWN, trits.UNKNOWN])
    assert list(trits.confirm(left, right)) == [trits.NORMAL, trits.UNKNOWN, trits.UNKNOWN]


# ------------------------------------------------- семантический контракт

def test_contract_covers_known_pairs():
    table = contract.load_contract()
    assert len(table) > 100, "контракт подозрительно мал"


@pytest.mark.parametrize("sensor_type,value,expected", [
    ("Датчик дыма", "Норма", contract.NORM),
    ("Датчик дыма", "Обнаружен дым", contract.TRIGGER),
    ("Датчик дыма", "Неисправен", contract.FAULT),
    ("Датчик дыма", "Неопределен", contract.UNDEFINED),
    ("Датчик дыма", "Выключен", contract.DISABLED),
])
def test_contract_classifies_known_values(sensor_type, value, expected):
    assert contract.classify(sensor_type, value) == expected


def test_unknown_pair_falls_back_to_garbage_not_to_norm():
    """Незнакомое значение не должно молча считаться нормой."""
    assert contract.classify("Датчик дыма", "нечто невиданное") != contract.NORM


def test_sentinels_are_not_measurements():
    """Служебные коды производителя не должны попадать в телеметрию."""
    assert contract.is_sentinel(-127.0) or contract.is_sentinel(999.0)


def test_contract_maps_classes_to_trits():
    assert contract.CLASS_TO_TRIT[contract.NORM] == trits.NORMAL
    assert contract.CLASS_TO_TRIT[contract.TRIGGER] == trits.DEVIATION
    assert contract.CLASS_TO_TRIT[contract.FAULT] == trits.DEVIATION
    assert contract.CLASS_TO_TRIT[contract.UNDEFINED] == trits.UNKNOWN


def test_failure_classes_do_not_include_norm():
    assert contract.NORM not in contract.FAILURE_CLASSES
    assert contract.FAULT in contract.FAILURE_CLASSES


# ------------------------------------------------------------- диаграммы

def test_scenario_palette_is_fixed_and_complete():
    """Цвет закреплён за сценарием: он не должен зависеть от сортировки."""
    assert set(charts.SCENARIO) == {"отказ", "подтопление", "пожар", "проникновение"}
    assert len(set(charts.SCENARIO.values())) == 4


def test_recall_chart_uses_numeric_axis():
    """На категориальной оси участок 30–50 % выглядел бы как 10–20 %."""
    rows = [{"recall": r, "alerts_per_day": a, "precision": p}
            for r, a, p in ((0.1, 14, 0.031), (0.3, 250, 0.005), (0.5, 713, 0.003))]
    option = charts.recall_cost(rows)
    assert option["xAxis"]["type"] == "value"
    assert option["series"][0]["data"][0] == [10, 14]


def test_charts_never_use_two_value_axes():
    """Две шкалы на одном поле создают корреляцию, которой нет в данных."""
    built = [
        charts.horizon([{"label": "24 часа", "best_precision": 0.18}]),
        charts.observability([{"type": "тепловой", "objects_without": 13}], 16),
        charts.incubation([{"label": "сегодня", "count": 5}]),
        charts.evidence([{"short": "улика", "contribution": 0.1}]),
    ]
    for option in built:
        for axis_name in ("xAxis", "yAxis"):
            assert isinstance(option[axis_name], dict), "ось не должна быть списком осей"


def test_stacked_chart_has_legend_for_several_series():
    option = charts.by_object(["объект А"], ["отказ", "пожар"],
                              {"отказ": {"объект А": 2}, "пожар": {"объект А": 1}})
    assert "legend" in option
    assert len(option["series"]) == 2


def test_incubation_buckets_cover_all_values():
    values = [0, 1, 7, 8, 30, 31, 60, 61, 100, None]
    buckets = charts.incubation_buckets(values)
    assert sum(b["count"] for b in buckets) == len([v for v in values if v is not None])


# --------------------------------------------------------- форматы обмена

@pytest.fixture
def sample_rows():
    return [{
        "id": 1, "created_at": None, "scenario": "отказ", "object_id": 42,
        "object_name": "объект Гамма", "picket": 906, "channel_id": 330246,
        "probability": 0.9764, "horizon_hours": 24, "status": "новый",
        "card": {
            "title": "Прогноз отказа контакта АВ на объекте Гамма, ПК906",
            "evidence": [{"feature": "fault_ev_7d", "contribution": 0.115,
                          "phrase": "сообщений о неисправности за 7 суток: 1",
                          "since": "2026-05-06T14:45:35"}],
            "blind_spots": ["тепловой режим не контролируется"],
            "timeline": [{"ts": "2026-05-06T14:45:35", "kind": "признак",
                          "text": "сообщений о неисправности за 7 суток: 1"}],
            "recommendation": "Проверить питание и линию связи канала.",
            "first_sign_at": "2026-05-06T14:45:35", "incubation_days": 0,
        },
    }]


def test_csv_opens_in_russian_excel(sample_rows):
    """Точка с запятой и метка BOM — иначе строка склеится в одну ячейку."""
    data = exchange.to_csv(sample_rows)
    assert data.startswith(b"\xef\xbb\xbf"), "нет метки BOM"
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 2
    assert rows[0][0] == "идентификатор"
    assert "объект Гамма" in rows[1]


def test_xml_keeps_card_structure(sample_rows):
    root = ET.fromstring(exchange.to_xml(sample_rows))
    assert root.tag == "verdicts" and root.get("count") == "1"
    verdict = root[0]
    assert verdict.find("scenario").text == "отказ"
    evidence = verdict.find("evidence")
    assert len(evidence) == 1
    assert evidence[0].get("contribution") is not None
    assert len(verdict.find("timeline")) == 1


def test_geojson_coordinates_are_longitude_first():
    """Перепутанный порядок увозит Москву в Индийский океан."""
    geometry = [{
        "object_name": "объект Гамма", "toponym": "Ленинский",
        "anchor_known": True, "picket_min": 0, "picket_max": 100,
        "length_m": 1000, "n_cards": 1, "max_probability": 0.9,
        "line": [[55.63, 37.52], [55.74, 37.56]],
        "points": [{"lat": 55.71, "lon": 37.55, "scenario": "отказ",
                    "probability": 0.9, "title": "т", "picket": 906}],
    }]
    collection = exchange.to_geojson(geometry)
    assert collection["type"] == "FeatureCollection"
    line = collection["features"][0]["geometry"]["coordinates"]
    assert 36 < line[0][0] < 38, "первой должна идти долгота"
    assert 54 < line[0][1] < 57, "второй должна идти широта"
    point = collection["features"][1]["geometry"]["coordinates"]
    assert point == [37.55, 55.71]


def test_wkt_is_a_table_with_wkt_column():
    geometry = [{"object_name": "объект Гамма", "line": [[55.6, 37.5], [55.7, 37.6]],
                 "max_probability": 0.9, "points": []}]
    text = exchange.to_wkt(geometry).decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].split(";")[0] == "wkt"
    assert lines[1].startswith("LINESTRING(37.5")


# ------------------------------------------------------- заявки и календарь

def test_cascade_always_raises_priority_to_emergency():
    """Отказ питающей линии обесточивает участок — плановый обход не годится."""
    assert orders.derive_priority(0.1, "отказ", incubation_days=60,
                                  cascade=True) == "аварийный"


def test_priority_grows_with_probability():
    low = orders.derive_priority(0.2, "отказ")
    high = orders.derive_priority(0.9, "отказ")
    assert orders.PRIORITIES.index(high) > orders.PRIORITIES.index(low)


def test_due_date_shifts_before_long_holidays():
    """Заявка не должна провисеть все новогодние каникулы."""
    from core import calendar_risk
    due, note = calendar_risk.adjust_due_date("2026-01-05", "2025-12-28")
    assert note is not None and "сдвинут" in note


def test_title_declension():
    title = cards.build_title("отказ", sensor_name="МРСБ АВ ПК906",
                              sensor_type="КД АВ", object_name="объект Гамма",
                              picket=906)
    assert "на объекте Гамма" in title
    assert "МРСБ АВ ПК906" in title, "технические имена не должны терять регистр"
