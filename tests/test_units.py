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


# ------------------------------------------- изоляция синтетического объекта

def test_synthetic_verdict_cannot_be_acknowledged():
    """Решения диспетчера — будущая настоящая разметка.

    Вердикты экспериментального объекта живут в файле и имеют отрицательные
    идентификаторы. Если по такому вердикту можно будет зафиксировать
    решение, выдуманные события попадут в таблицу decision и отравят ровно
    те данные, которыми проект обещает заменить синтез. Проверка стоит
    в слое доступа к данным, а не в интерфейсе, и тест это закрепляет.
    """
    from core import db
    for bad in (-1, -42, 0, None):
        with pytest.raises(ValueError):
            db._real_verdict(bad)


def test_real_verdict_passes_through():
    from core import db
    assert db._real_verdict(17) == 17
    assert db._real_verdict("23") == 23


def test_work_order_rejects_synthetic_verdict():
    """Черновик заявки по синтетическому вердикту тоже недопустим."""
    from core import db
    with pytest.raises(ValueError):
        db.create_work_order(None, {"verdict_id": -3, "work_type": "осмотр",
                                    "priority": "обычный"})


# ------------------------------- устойчивость страницы к версии файла данных

def test_experiment_truth_reads_both_file_versions():
    """В образ копируется seed-файл, и он может оказаться старее кода.

    В прежней версии суммарная истина звалась «истина» и хранила списки
    событий, а не их число. Падение страницы здесь означало бы пустой
    экран вместо ответа на вопрос организаторов, поэтому обе версии
    обязаны читаться.
    """
    new = {"истина_суммарно": {"failures": 442, "fires": 18}}
    assert cards.experiment_truth(new) == {"failures": 442, "fires": 18}

    old = {"истина": {"failures": [1, 2, 3], "fires": [{"picket": 7}]}}
    assert cards.experiment_truth(old) == {"failures": 3, "fires": 1}

    assert cards.experiment_truth({}) == {}
    assert cards.experiment_truth(None) == {}


def test_incident_value_degrades_instead_of_crashing():
    """Метрики подтверждения появились позже — их может не быть в файле."""

    row = {"инциденты": {"задымление": {"precision": 0.022,
                                        "precision_подтверждённых": 1.0}}}
    assert cards.incident_value(row, "задымление", "precision") == 0.022
    assert cards.incident_value(row, "задымление", "recall_подтверждённых") == 0.0
    assert cards.incident_value(row, "подтопление", "precision") == 0.0
    assert cards.incident_value({}, "задымление", "precision") == 0.0
    assert cards.incident_value(row, "задымление", "суток_прогона_ТО", 0) == 0


# --------------------------------- выбор рекомендуемой конфигурации

def _cfg(name, channels, recall, precision, incidents):
    """Строка сетки конфигураций в том виде, в каком её пишет эксперимент."""
    full = {k: {"precision_подтверждённых": 0.0, "recall_подтверждённых": 0.0}
            for k in cards.SCENARIOS}
    full.update(incidents)
    return {"конфигурация": name, "каналов": channels,
            "отказы": {"recall@сутки": recall, "precision@50": precision},
            "инциденты": full}


def test_recommendation_prefers_closing_targets_over_total_score():
    """Вопрос организаторов — что поставить, чтобы ПОКАЗАТЕЛИ достигли
    требуемых. Поэтому сначала число закрытых сценариев, потом цена.

    Прежнее правило складывало метрики в один балл и склонялось к ответу
    «поставьте всё»: богатая конфигурация почти всегда набирает больше
    баллов, но из этого не следует, что остальное нужно.
    """
    grid = [
        _cfg("как сейчас", 400, 0.23, 0.03, {}),
        # богатая, но ни один порог не взят
        _cfg("много датчиков", 1800, 0.60, 0.05,
             {k: {"precision_подтверждённых": 0.4,
                  "recall_подтверждённых": 0.45} for k in cards.SCENARIOS}),
        # скромная, но закрывает сценарий целиком
        _cfg("+ тепловой контроль", 600, 0.15, 0.04,
             {"задымление": {"precision_подтверждённых": 1.0,
                             "recall_подтверждённых": 0.55}}),
    ]
    best = cards.recommended(grid)
    assert best["конфигурация"] == "+ тепловой контроль", \
        "выбрана конфигурация с большим баллом вместо закрывающей порог"
    assert cards.targets_met(best) == 1


def test_recommendation_takes_cheapest_among_equally_closing():
    grid = [
        _cfg("дорогая", 2000, 0.15, 0.04,
             {"задымление": {"precision_подтверждённых": 1.0,
                             "recall_подтверждённых": 0.55}}),
        _cfg("дешёвая", 700, 0.15, 0.04,
             {"задымление": {"precision_подтверждённых": 0.9,
                             "recall_подтверждённых": 0.52}}),
    ]
    assert cards.recommended(grid)["конфигурация"] == "дешёвая"


def test_recommendation_falls_back_when_nothing_closes():
    """Если не закрыт ни один сценарий, ответ слабее — но он есть."""
    grid = [
        _cfg("как сейчас", 400, 0.23, 0.03, {}),
        _cfg("побогаче", 600, 0.40, 0.05,
             {"задымление": {"precision_подтверждённых": 0.3,
                             "recall_подтверждённых": 0.3}}),
    ]
    best = cards.recommended(grid)
    assert cards.targets_met(best) == 0
    assert best["конфигурация"] == "побогаче"


def test_targets_met_requires_both_thresholds():
    """Одной точности недостаточно: нужна и полнота."""
    only_precision = _cfg("точность", 500, 0.1, 0.1,
                          {"задымление": {"precision_подтверждённых": 1.0,
                                          "recall_подтверждённых": 0.39}})
    only_recall = _cfg("полнота", 500, 0.1, 0.1,
                       {"задымление": {"precision_подтверждённых": 0.5,
                                       "recall_подтверждённых": 0.8}})
    assert cards.targets_met(only_precision) == 0
    assert cards.targets_met(only_recall) == 0


# ------------------------------------------------------- словарь терминов

def test_glossary_explains_every_term_it_uses():
    """Объяснение не должно вводить слово, которого нет в словаре.

    Правило простое: если в объяснении встречается служебное имя признака
    или редкий термин, он обязан быть объяснён здесь же. Проверяется
    на именах признаков — именно они режут глаз непод­готовленному
    читателю.
    """
    from core import glossary
    from core.features import FEATURE_NAMES

    explained = " ".join(t for t in glossary.terms())
    body = " ".join(m for _, items in glossary.GLOSSARY for _, m in items)
    for name in FEATURE_NAMES:
        if name in body:
            assert name in explained, \
                f"признак {name} упомянут в объяснении, но сам не объяснён"


def test_glossary_has_no_empty_entries():
    from core import glossary
    for title, items in glossary.GLOSSARY:
        assert title.strip(), "группа без названия"
        assert items, f"пустая группа «{title}»"
        for term, meaning in items:
            assert term.strip(), f"термин без названия в «{title}»"
            assert len(meaning) > 40, \
                f"объяснение «{term}» слишком короткое, чтобы что-то объяснить"


def test_glossary_document_is_in_sync():
    """Словарь в PDF собирается из того же источника, что страница.

    Если документ отстал, расхождение найдёт эксперт, а не мы. Лечится
    командой `python -m scripts.15_sync_glossary`.
    """
    import os
    from core import config, glossary

    path = os.path.join(config.ROOT, "docs", "17-повышение-точности.md")
    with open(path, encoding="utf-8") as fh:
        doc = fh.read()
    missing = [t for t in glossary.terms() if f"**{t}.**" not in doc]
    assert not missing, (
        "документ отстал от словаря, не хватает: "
        + ", ".join(missing[:5])
        + ". Запустите python -m scripts.15_sync_glossary")


def test_experiment_table_is_in_sync_with_documents():
    """Таблица конфигураций в документах собирается из того же расчёта.

    Однажды подпись «дым: доля подтв.» означала на экране долю настоящих
    среди всех сработок (2,2 %), а в записке — долю настоящих среди
    подтверждённых (0 или 100 %). Один столбец показывал разные числа
    в двух документах комплекта. Лечится командой
    `python -m scripts.16_sync_experiment_tables`.
    """
    import os
    from core import config, glossary

    for name, keys in (("17-повышение-точности.md",
                        list(glossary.GRID_LABELS)),
                       ("13-пояснительная-записка.md",
                        ["конфигурация", "каналов", "отказы_полнота",
                         "дым_подтв_точность", "дым_подтв_полнота"])):
        path = os.path.join(config.ROOT, "docs", name)
        with open(path, encoding="utf-8") as fh:
            doc = fh.read()
        for key in keys:
            label = glossary.GRID_LABELS[key].capitalize()
            assert label in doc, (
                f"{name}: нет столбца «{label}». Запустите "
                "python -m scripts.16_sync_experiment_tables")


def test_grid_labels_do_not_use_ambiguous_abbreviations():
    """Подписи столбцов не сокращаются: сокращение читается каждым по-своему.

    «доля подтв.» можно прочитать и как долю подтверждённых среди
    настоящих, и как долю настоящих среди подтверждённых. Решение
    о закупке принимают по этой таблице, поэтому подписи длинные.
    """
    from core import glossary

    for key, label in glossary.GRID_LABELS.items():
        assert "подтв." not in label, f"сокращение в подписи «{label}»"
        assert "(синт.)" not in label, f"непонятная пометка в «{label}»"
    # величины про дым должны называть, что именно считается
    smoke = [v for k, v in glossary.GRID_LABELS.items() if k.startswith("дым")]
    assert len(smoke) == 4
    assert len(set(smoke)) == 4, "две колонки про дым названы одинаково"
