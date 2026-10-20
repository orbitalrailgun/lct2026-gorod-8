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


# ------------------------------------------------- страница «Точность»

def test_improve_page_states_the_question(dispatcher, base_url):
    """Страница обязана начинаться с вопроса, а не с ответа."""
    ui.open_page(dispatcher, base_url, "/improve",
                 expect="Повышение точности прогноза")
    body = dispatcher.find_element("tag name", "body").text
    assert "датчиков" in body
    assert "синтетическ" in body.lower(), "нет оговорки о синтетике"


def test_improve_page_separates_new_failures_from_continuation(dispatcher, base_url):
    """Главная поправка на честность должна быть на экране.

    Заявленные 18 % держатся на каналах, которые уже сообщают о
    неисправности. Если это разделение исчезнет со страницы, рекомендация
    по дооснащению будет опираться на цифру, измеряющую не то, что
    заказчик собирается покупать.
    """
    ui.open_page(dispatcher, base_url, "/improve", expect="Повышение точности")
    body = dispatcher.find_element("tag name", "body").text
    assert "Новый отказ" in body, "нет популяции новых отказов"
    assert "Продолжение" in body, "нет популяции продолжающихся отказов"
    assert "77" in body and "23" in body, "нет долей популяций"


def test_improve_page_draws_experiment_charts(dispatcher, base_url):
    ui.open_page(dispatcher, base_url, "/improve", expect="Повышение точности")
    ui.wait(dispatcher, lambda d: len(ui.canvases(d)) >= 3,
            message="диаграммы страницы точности не отрисованы")


def test_improve_page_does_not_claim_precision_for_incidents(dispatcher, base_url):
    """Для инцидентов Precision сервиса не существует, и страница это говорит.

    Записка обещает организаторам, что Precision детектора не заявляется:
    разметки нет. Если страница назовёт синтетическую величину точностью
    сервиса, два документа комплекта будут противоречить друг другу.
    """
    ui.open_page(dispatcher, base_url, "/improve", expect="Повышение точности")
    body = dispatcher.find_element("tag name", "body").text
    assert "доля подтверждённых" in body.lower(), \
        "величина для инцидентов названа не так, как обещано в записке"


def test_experiment_verdicts_are_marked_and_not_actionable(dispatcher, base_url):
    """Синтетика в очереди: помечена и без кнопок фиксации решения."""
    ui.open_page(dispatcher, base_url, "/", expect="")
    body = dispatcher.find_element("tag name", "body").text
    if "синтетическ" not in body.lower():
        pytest.skip("вердикты экспериментального объекта не загружены")
    assert "в счётчики, отчёты и выгрузки не входят" in body
    assert "решения по ним не фиксируются" in body, \
        "на уровне объекта не сказано, что решения не фиксируются"

    # Та же метка обязана быть и на самой карточке вердикта, иначе
    # диспетчер увидит её только если прочитает заголовок блока.
    # Карточки наполняются по клику, поэтому блок раскрывается.
    ui.expand(dispatcher, "Вердикты объекта", index=-1,
              appears="решения не фиксируются")
    assert "экспериментальный объект: решения не фиксируются" in \
        dispatcher.find_element("tag name", "body").text, \
        "по синтетическому вердикту предлагается зафиксировать решение"


def test_improve_page_answers_before_it_explains(dispatcher, base_url):
    """Вывод должен быть виден до статистики.

    Страницу читают, чтобы принять решение о закупке. Если ответ окажется
    после выкладок, его не прочитают — поэтому блок «Коротко» стоит
    первым, и проверяется именно порядок, а не наличие.
    """
    ui.open_page(dispatcher, base_url, "/improve", expect="Повышение точности")
    body = dispatcher.find_element("tag name", "body").text
    assert "Коротко" in body, "нет короткого ответа"
    assert body.index("Коротко") < body.index("канало-суток"), \
        "вывод оказался после статистики"
    assert "Если нужна одна строка для решения" in body


def test_improve_page_glossary_opens_and_explains(dispatcher, base_url):
    """Словарь скрыт, но раскрывается и объясняет термины страницы.

    Наполнение ленивое, поэтому до раскрытия его в разметке нет —
    так и задумано: сорок с лишним пояснений на каждой загрузке
    страницы это лишний вес.
    """
    ui.open_page(dispatcher, base_url, "/improve", expect="Повышение точности")
    body = dispatcher.find_element("tag name", "body").text
    assert "пояснени" in body, "нет блока словаря"
    assert "Канало-сутки" not in body, "словарь отрисован до раскрытия"

    ui.expand(dispatcher, "Что значат термины", appears="Пикет")
    opened = dispatcher.find_element("tag name", "body").text
    for term in ("Пикет", "Канало-сутки", "Самодиагностика", "Плацебо",
                 "Доля подтверждённых", "Порог срабатывания"):
        assert term in opened, f"в словаре нет термина «{term}»"
    assert "десять метров" in opened, "пикет объяснён без единиц"


def test_improve_page_glossary_covers_terms_used_on_page(dispatcher, base_url):
    """Каждое служебное имя, попавшее на страницу, должно быть объяснено."""
    from core import glossary

    ui.open_page(dispatcher, base_url, "/improve", expect="Повышение точности")
    page = dispatcher.find_element("tag name", "body").text
    ui.expand(dispatcher, "Что значат термины", appears="Пикет")
    explained = dispatcher.find_element("tag name", "body").text

    for name in ("domain_size", "domain_dev_share_7d", "domain_silent_share"):
        if name in page:
            assert name in explained, \
                f"{name} показан на странице, но не объяснён в словаре"
    assert len(glossary.terms()) >= 40, "словарь подозрительно короткий"
