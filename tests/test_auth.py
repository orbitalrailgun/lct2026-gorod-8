"""Тесты входа и разграничения прав — через настоящий браузер.

Ролевая модель проверяется здесь, а не в юнит-тестах, намеренно: до 27.09
право фиксации решений было объявлено в таблице прав, но не применялось
в интерфейсе. Тест на словарь прав прошёл бы, а техник продолжал бы видеть
кнопку «Решение». Проверять нужно то, что видит и может человек.
"""

import pytest

from tests import ui

pytestmark = pytest.mark.ui


def test_anonymous_is_redirected_to_login(anonymous, base_url):
    """Без входа любая страница уводит на форму: ждать надо именно форму,
    заголовок сервиса есть и в пустой оболочке страницы."""
    ui.open_page(anonymous, base_url, "/", expect="одноразовый код")
    assert "Очередь на проверку" not in anonymous.page_source


def test_login_page_offers_all_four_roles(anonymous, base_url):
    ui.open_page(anonymous, base_url, "/login", expect="Дискреция творца")
    from selenium.webdriver.common.by import By
    field = anonymous.find_element(
        By.XPATH, "//input[@aria-label='пользователь']"
                  "/ancestor::*[contains(@class,'q-select')][1]")
    field.click()
    # Ждать надо не появления элементов, а появления в них текста:
    # список открывается с анимацией, и первый опрос застаёт его пустым.
    items = ui.wait(anonymous, lambda d: (
        lambda found: found if len(found) == 4 and all(i.text.strip() for i in found)
        else False)(d.find_elements(By.CSS_SELECTOR, ".q-menu .q-item")),
        message="список пользователей не раскрылся")
    titles = [i.text for i in items]
    assert len(titles) == 4
    for expected in ("Техник", "Диспетчер района", "Диспетчер ОДС",
                     "Группа реагирования"):
        assert any(expected in t for t in titles), f"нет роли {expected}"


@pytest.mark.parametrize("role", list(ui.ROLES))
def test_each_role_can_log_in(as_role, role):
    driver = as_role(role)
    assert "Очередь на проверку" in driver.page_source
    assert ui.ROLES[role].split()[-1] in driver.page_source


def test_wrong_code_is_rejected(anonymous, base_url):
    ui.open_page(anonymous, base_url, "/login", expect="Дискреция творца")
    ui.select_option(anonymous, "пользователь", "Диспетчер ОДС")
    ui.fill(anonymous, "одноразовый код", "000000")
    ui.click_button(anonymous, "Войти")
    ui.wait_text(anonymous, "неверный одноразовый код")
    assert "Очередь на проверку" not in anonymous.page_source


def test_dispatcher_sees_settings_and_technician_does_not(as_role):
    ods = as_role("диспетчер_одс")
    assert "Настройки" in ods.page_source

    technician = as_role("техник")
    assert "Настройки" not in technician.page_source


def test_technician_is_denied_admin_page(technician, base_url):
    ui.open_page(technician, base_url, "/admin", expect="Недостаточно прав")
    assert "Журнал действий" not in technician.page_source


def test_ods_gets_admin_page(ods, base_url):
    ui.open_page(ods, base_url, "/admin", expect="Настройки сервиса")
    assert "Журнал действий" in ods.page_source
    assert "Горизонт прогноза" in ods.page_source


def test_scope_limits_what_technician_sees(as_role):
    """Область видимости задаётся назначениями, а не ролью."""
    ods = as_role("диспетчер_одс")
    ui.wait_text(ods, "Объектов в очереди")
    wide = ui.count_text(ods, r"Вердикты объекта")

    technician = as_role("техник")
    ui.wait_text(technician, "Объектов в очереди")
    narrow = ui.count_text(technician, r"Вердикты объекта")

    assert narrow > 0, "техник не видит ни одного назначенного объекта"
    assert narrow < wide, "область видимости техника не ограничена"


def test_logout_closes_session(ods, base_url):
    from selenium.webdriver.common.by import By
    icons = [b for b in ods.find_elements(By.CSS_SELECTOR, "button")
             if "logout" in b.text.lower() or "logout" in (b.get_attribute("innerHTML") or "")]
    assert icons, "нет кнопки выхода"
    icons[-1].click()
    ui.wait_text(ods, "одноразовый код")
