"""Сквозной сценарий диспетчера: очередь → квитирование → решение → заявка.

Эти тесты меняют данные стенда: вердикт переходит в статус «в работе»,
затем «закрыт», и появляется черновик заявки. Так и задумано — проверять
замкнутый цикл, не меняя состояния, невозможно.

Демонстрационный стенд возвращается в исходное состояние двумя командами,
они описаны в README и в разделе 13 записки:

    docker compose exec db psql -U concorde -c "TRUNCATE verdict CASCADE;"
    docker compose exec app python -m scripts.06_seed_db
"""

import re

import pytest

from tests import ui

pytestmark = [pytest.mark.ui, pytest.mark.mutating]


@pytest.fixture(autouse=True)
def _stand(fresh_verdicts):
    """Перед сценарными тестами стенд возвращается в исходное состояние."""
    return fresh_verdicts


def open_actionable_card(driver):
    """Раскрыть мультикарточку, в которой есть неотработанный вердикт.

    Брать всегда первую нельзя: предыдущий прогон мог её закрыть,
    и тест упал бы по причине, не имеющей отношения к приложению.
    """
    from selenium.webdriver.common.by import By
    blocks = driver.find_elements(By.CSS_SELECTOR, ".q-expansion-item")
    titles = [b for b in blocks if "Вердикты объекта" in b.text]
    assert titles, "в очереди нет ни одной мультикарточки"

    for index in range(len(titles)):
        ui.expand(driver, "Вердикты объекта", appears="Почему так решено")
        if ui.buttons(driver, "Квитировать"):
            return True
        driver.refresh()
        ui.wait_text(driver, "Объектов в очереди")
    return False


def open_first_card(driver):
    """Раскрыть первую мультикарточку и карточки вердиктов в ней."""
    ui.expand(driver, "Вердикты объекта", appears="Почему так решено")


def test_acknowledge_takes_verdict_into_work(dispatcher):
    assert open_actionable_card(dispatcher), "нет ни одного неотработанного вердикта"
    ui.click_button(dispatcher, "Квитировать")
    ui.wait_text(dispatcher, "взят в работу")


def test_decision_closes_verdict_and_reaches_journal(dispatcher, base_url):
    open_first_card(dispatcher)
    ui.click_button(dispatcher, "Решение")
    ui.wait_text(dispatcher, "Решение диспетчера")

    ui.select_option(dispatcher, "действие", "выезд")
    ui.select_option(dispatcher, "причина", "Неисправность подтверждена")
    ui.click_button(dispatcher, "Сохранить")
    ui.wait_text(dispatcher, "Решение зафиксировано")

    ui.open_page(dispatcher, base_url, "/journal", expect="Журнал прогнозов")
    body = dispatcher.find_element("tag name", "body").text
    assert "закрыт" in body, "решение не отразилось в журнале"
    assert "Неисправность подтверждена" in body or "подтверждено" in body


def test_work_order_draft_is_created_from_card(dispatcher, base_url):
    open_first_card(dispatcher)
    ui.click_button(dispatcher, "Заявка")
    ui.wait_text(dispatcher, "Черновик заявки на ремонт")

    assert "тип работ" in dispatcher.page_source
    assert "исполнитель" in dispatcher.page_source

    ui.click_button(dispatcher, "Создать черновик")
    ui.wait_text(dispatcher, "Черновик заявки")

    ui.open_page(dispatcher, base_url, "/orders", expect="Черновики заявок")
    body = dispatcher.find_element("tag name", "body").text
    assert "Проверка" in body or "Обследование" in body or "Осмотр" in body, \
        "заявка не появилась в реестре"


def test_order_justification_comes_from_the_card(dispatcher, base_url):
    """Обоснование переносится из карточки, а не набирается заново."""
    ui.open_page(dispatcher, base_url, "/orders", expect="Черновики заявок")
    # В реестре блок называется коротко; полная подпись — в диалоге создания.
    block = ui.expand(dispatcher, "Обоснование")
    text = block.text
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text), \
        "в обосновании нет меток времени из карточки"
    assert "неисправност" in text or "событий" in text or "молчит" in text, \
        "в обосновании нет улик из карточки"


def test_priority_and_due_date_are_derived(dispatcher, base_url):
    ui.open_page(dispatcher, base_url, "/orders", expect="Черновики заявок")
    body = dispatcher.find_element("tag name", "body").text
    assert re.search(r"(аварийный|высокий|средний|низкий)", body), "нет приоритета"
    assert re.search(r"\d{2}\.\d{2}\.\d{4}", body), "нет срока выполнения"


def test_technician_cannot_reach_decision_dialog(technician):
    open_first_card(technician)
    assert "режим просмотра" in technician.page_source
    assert not ui.buttons(technician, "Решение"), "техник видит кнопку решения"
    assert not ui.buttons(technician, "Заявка"), "техник видит кнопку заявки"


def test_audit_log_records_actions(ods, base_url):
    """Журналирование действий — требование раздела 11 ТЗ."""
    ui.open_page(ods, base_url, "/admin", expect="Журнал действий")
    body = ods.find_element("tag name", "body").text
    assert "вход в систему" in body
    assert any(word in body for word in ("решение", "квитирование", "черновик заявки")), \
        "действия смены не попали в журнал"
