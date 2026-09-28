"""Тесты очереди и карточки вердикта.

Карточка — главное, чем пользуется диспетчер, поэтому проверяется
не только её наличие, но и содержание: есть ли улики с вкладами,
заявлены ли слепые зоны, строится ли диаграмма при раскрытии.

Отдельно проверяется ленивое наполнение. Оно появилось после замера,
показавшего 11 секунд на двадцати одновременных пользователях, и легко
ломается при неосторожной правке: содержимое просто перестанет
появляться, а тест на наличие текста в исходной разметке этого
не заметит — его там нет и не должно быть.
"""

import re

import pytest

from tests import ui

pytestmark = pytest.mark.ui


def test_queue_shows_counters(dispatcher):
    for title in ("Объектов в очереди", "Вердиктов", "Высокий риск",
                  "Не отработано", "Ограниченная наблюдаемость"):
        assert title in dispatcher.page_source, f"нет счётчика «{title}»"


def test_queue_groups_verdicts_by_object(dispatcher):
    """Бригада выезжает на объект, поэтому карточки собраны в мультикарточки."""
    assert ui.count_text(dispatcher, r"Вердикты объекта") > 0
    assert ui.count_text(dispatcher, r"Общая хронология объекта") > 0


def test_multicard_content_is_lazy(dispatcher):
    """До раскрытия карточек вердиктов в разметке быть не должно."""
    assert "Почему так решено" not in dispatcher.page_source

    ui.expand(dispatcher, "Вердикты объекта", appears="Почему так решено")
    assert "Почему так решено" in dispatcher.page_source


def test_verdict_card_has_all_required_parts(dispatcher):
    ui.expand(dispatcher, "Вердикты объекта", appears="Почему так решено")
    ui.expand(dispatcher, "Почему так решено", appears="Базовая вероятность")

    source = dispatcher.page_source
    assert re.search(r"Прогноз \w+", source), "нет описательного заголовка"
    assert re.search(r"[+−-]\d+[,.]\d\s*%", source), "нет вкладов улик"
    assert "Базовая вероятность" in source
    assert re.search(r"за \d+ ч", source), "не указан горизонт"


def test_evidence_chart_is_drawn_on_expand(dispatcher):
    """Диаграмма вкладов строится по раскрытию, а не заранее."""
    before = len(ui.canvases(dispatcher))
    ui.expand(dispatcher, "Вердикты объекта", appears="Почему так решено")
    ui.expand(dispatcher, "Почему так решено", appears="Базовая вероятность")
    ui.wait(dispatcher, lambda d: len(ui.canvases(d)) > before,
            message="диаграмма вкладов не появилась")


def test_timeline_marks_are_second_precise(dispatcher):
    """Секундная точность — то, что делает видимыми каскады."""
    ui.expand(dispatcher, "Общая хронология объекта", appears="признак")
    # Метка выводится в ISO-виде без буквы T: 2026-05-06 14:45:35
    marks = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", dispatcher.page_source)
    assert marks, "метки времени без секунд"
    assert not all(m.endswith("00:00:00") for m in marks), \
        "все метки в полночь — значит, секундная точность потеряна"


def test_blind_spots_are_declared_somewhere(dispatcher):
    """Сервис обязан говорить, чего он не видит."""
    ui.expand(dispatcher, "Вердикты объекта", appears="Почему так решено")
    assert ("Чего мы не видим" in dispatcher.page_source
            or "ограничена наблюдаемость" in dispatcher.page_source)


def test_scenario_filter_narrows_the_queue(dispatcher):
    ui.select_option(dispatcher, "сценарий", "отказы")
    ui.wait_text(dispatcher, "Объектов в очереди")
    only_failures = dispatcher.page_source
    assert "отказ" in only_failures


def test_queue_text_has_no_unagreed_numerals(dispatcher):
    """Проверка на настоящем экране, а не на шаблонах."""
    ui.expand(dispatcher, "Вердикты объекта", appears="Почему так решено")
    text = dispatcher.find_element("tag name", "body").text
    for pattern, wrong in (
        (r"(?<!\d)1 вердиктов", "«1 вердиктов»"),
        (r"(?<!\d)1 записей", "«1 записей»"),
        (r"(?<!\d)[234] записей", "«22 записей»"),
        (r"(?<!\d)1 суток", "«1 суток»"),
        (r"(?<!\d)1 сообщений", "«1 сообщений»"),
        (r"в [234] раз выше", "«в 2 раз выше»"),
    ):
        assert not re.search(pattern, text), f"на экране встречается {wrong}"


def test_technician_sees_no_action_buttons(technician):
    """Роль без права решения работает в режиме просмотра."""
    ui.expand(technician, "Вердикты объекта", appears="Почему так решено")
    source = technician.page_source
    assert "режим просмотра" in source
    assert "Квитировать" not in source
    assert ">Решение<" not in source
