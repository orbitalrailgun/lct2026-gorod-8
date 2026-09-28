"""Тесты остальных экранов: схема, карта, аналитика, журнал, настройки.

Проверяется не только то, что страница открылась, но и что на ней есть
содержимое: линии на схеме, слой карты, отрисованные диаграммы, строки
в таблицах. Пустая страница отвечает двести точно так же, как полная.
"""

import re

import pytest

from tests import ui

pytestmark = pytest.mark.ui


def test_scheme_draws_pickets(dispatcher, base_url):
    ui.open_page(dispatcher, base_url, "/scheme", expect="Линейная схема коллекторов")
    source = dispatcher.page_source
    assert "<svg" in source, "схема не нарисована"
    assert re.search(r"ПК\d+", source), "нет отметок пикетов"
    assert "один пикет — 10 метров" in source or "1 пикет" in source or "10 метров" in source


def test_scheme_legend_lists_scenarios(dispatcher, base_url):
    ui.open_page(dispatcher, base_url, "/scheme", expect="Линейная схема")
    body = dispatcher.find_element("tag name", "body").text
    for scenario in ("отказ датчика", "подтопление", "задымление", "проникновение"):
        assert scenario in body, f"в легенде нет сценария «{scenario}»"


def test_map_renders_leaflet_layer(dispatcher, base_url):
    ui.open_page(dispatcher, base_url, "/map", expect="")
    ui.wait(dispatcher, lambda d: d.find_elements("css selector", ".leaflet-container"),
            message="карта не инициализировалась")
    ui.wait(dispatcher, lambda d: d.find_elements("css selector", ".leaflet-tile"),
            message="подложка карты не загрузилась")


def test_map_states_that_geometry_is_schematic(dispatcher, base_url):
    """Оговорка о синтетических координатах должна быть на экране, а не только в записке."""
    ui.open_page(dispatcher, base_url, "/map")
    body = dispatcher.find_element("tag name", "body").text
    assert "схематич" in body.lower() or "координат" in body.lower()


def test_analytics_draws_all_charts(ods, base_url):
    ui.open_page(ods, base_url, "/analytics", expect="Аналитика")
    for title in ("Достижимая точность по горизонтам", "Цена полноты",
                  "Объекты без контроля", "Как копятся признаки",
                  "Вердикты по объектам"):
        assert title in ods.page_source, f"нет диаграммы «{title}»"
    ui.wait(ods, lambda d: len(ui.canvases(d)) >= 5,
            message="отрисованы не все диаграммы")


def test_analytics_duplicates_numbers_in_tables(ods, base_url):
    """Требование доступности: значение должно быть доступно текстом."""
    ui.open_page(ods, base_url, "/analytics", expect="Цена полноты")
    body = ods.find_element("tag name", "body").text
    assert "проверок в сутки" in body
    assert re.search(r"\b713\b", body), "нет числа проверок при полноте 0,5"


def test_journal_lists_verdicts(dispatcher, base_url):
    ui.open_page(dispatcher, base_url, "/journal", expect="Журнал прогнозов")
    body = dispatcher.find_element("tag name", "body").text
    assert "сценарий" in body and "объект" in body
    assert re.search(r"\d+\s*%", body), "нет значений риска"


def test_settings_expose_adjustable_parameters(ods, base_url):
    """Пороги и горизонт — настройки, а не константы в коде."""
    ui.open_page(ods, base_url, "/admin", expect="Настройки сервиса")
    body = ods.find_element("tag name", "body").text
    for title in ("Горизонт прогноза", "Целевая точность", "Целевая полнота",
                  "Размер суточной очереди"):
        assert title in body, f"нет параметра «{title}»"


def test_report_button_is_available(ods):
    assert ui.buttons(ods, "Отчёт XLSX"), "нет кнопки выгрузки отчёта"


def test_every_page_answers_for_dispatcher(dispatcher, base_url):
    """Обход всех доступных роли страниц: ни одна не должна падать."""
    for path, marker in (("/", "Очередь на проверку"),
                         ("/scheme", "Линейная схема"),
                         ("/map", ""),
                         ("/journal", "Журнал прогнозов"),
                         ("/orders", "Черновики заявок"),
                         ("/analytics", "Аналитика")):
        ui.open_page(dispatcher, base_url, path, expect=marker or None)
        assert "Server error" not in dispatcher.page_source, f"ошибка на {path}"
        assert "Traceback" not in dispatcher.page_source, f"исключение на {path}"
