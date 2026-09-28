"""Обёртки над Selenium: ожидания, поиск по видимому тексту, вход в систему.

Интерфейс собран на NiceGUI поверх Quasar, и это определяет способ работы
с ним из теста. Поля здесь не обычные `<select>` и `<input>`, а составные
компоненты, поэтому искать их по типу элемента бесполезно — ищем по тому,
что видит человек: по подписи поля и по тексту кнопки.

Вторая особенность — страница живёт на веб-сокете и дособирается после
загрузки. Поэтому в модуле нет ни одного `sleep` фиксированной длины:
везде ожидание условия. Тест, который «обычно проходит», хуже падающего.
"""

import re

from selenium.common.exceptions import (ElementNotInteractableException,
                                        NoSuchElementException,
                                        StaleElementReferenceException,
                                        TimeoutException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

TIMEOUT = 20

ROLES = {
    "техник": "Техник Смирнов",
    "диспетчер_района": "Диспетчер района Петров",
    "диспетчер_одс": "Диспетчер ОДС Иванова",
    "группа_реагирования": "Группа реагирования",
}


# Интерфейс живёт на веб-сокете и перерисовывает куски страницы, поэтому
# найденный элемент может устареть между двумя опросами. Для ожидания это
# нормальная ситуация, а не ошибка: просто пробуем снова.
IGNORED = (NoSuchElementException, StaleElementReferenceException,
           ElementNotInteractableException)


def wait(driver, condition, timeout=TIMEOUT, message=""):
    return WebDriverWait(driver, timeout, ignored_exceptions=IGNORED) \
        .until(condition, message)


def wait_text(driver, text, timeout=TIMEOUT):
    """Дождаться появления текста на странице."""
    wait(driver, lambda d: text in d.page_source, timeout,
         f"на странице не появился текст: {text!r}")


def has_text(driver, text):
    return text in driver.page_source


def open_page(driver, base_url, path, expect=None):
    """Открыть страницу и дождаться её сборки."""
    driver.get(base_url.rstrip("/") + path)
    if expect:
        wait_text(driver, expect)
    else:
        wait(driver, lambda d: len(d.page_source) > 5000)
    return driver


def buttons(driver, label):
    """Кнопки с заданной подписью.

    Сравнение идёт построчно и без учёта регистра: Quasar рисует подпись
    заглавными, а если у кнопки есть иконка, её имя попадает в текст
    отдельной строкой — «check\nКВИТИРОВАТЬ». Сравнение целых строк
    на равенство такую кнопку не находит.
    """
    target = label.strip().upper()
    found = []
    for button in driver.find_elements(By.CSS_SELECTOR, "button"):
        if not button.is_displayed():
            continue
        lines = [line.strip().upper() for line in button.text.splitlines()]
        if target in lines:
            found.append(button)
    return found


def click_button(driver, label, timeout=TIMEOUT):
    found = wait(driver, lambda d: buttons(d, label) or False, timeout,
                 f"не найдена кнопка {label!r}")
    found[0].click()
    return found[0]


def click_text(driver, text, timeout=TIMEOUT):
    """Клик по элементу с заданным видимым текстом — для раскрывающихся блоков."""
    xpath = f"//*[contains(normalize-space(text()), {text!r})]"
    element = wait(driver, EC.element_to_be_clickable((By.XPATH, xpath)), timeout,
                   f"не найден кликабельный элемент с текстом {text!r}")
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    element.click()
    return element


def expand(driver, label, appears=None, timeout=TIMEOUT):
    """Раскрыть блок по его подписи и дождаться содержимого.

    Кликать нужно по строке заголовка блока, а не по текстовому узлу
    с подписью: у Quasar подпись лежит внутри разметки заголовка, и клик
    по ней не всегда доходит до обработчика. Заодно ждём не анимацию,
    а появление самого содержимого — оно строится лениво.
    """
    # Искать блок по всему его тексту нельзя: внешний блок содержит текст
    # вложенных, и клик закрывал бы родителя вместо раскрытия ребёнка.
    # Сравнивается только строка заголовка.
    def matching(d):
        found = []
        for item in d.find_elements(By.CSS_SELECTOR, ".q-expansion-item"):
            if not item.is_displayed():
                continue
            headers = item.find_elements(By.CSS_SELECTOR, ".q-item")
            if headers and label in headers[0].text:
                found.append((item, headers[0]))
        return found or False

    blocks = wait(driver, matching, timeout,
                  f"не найден раскрывающийся блок {label!r}")
    header = blocks[0][1]
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", header)
    header.click()

    # Раскрытие анимировано: сразу после клика содержимое уже «видимо»,
    # но имеет нулевую высоту, и его текст не попадает в замер. Ждём,
    # пока блок действительно развернётся. Блок ищем заново на каждом
    # опросе: лента перерисовывается, и ссылка на элемент устаревает.
    def opened(d):
        for item, head in matching(d) or []:
            # Только собственное содержимое блока, не содержимое вложенных:
            # у Quasar контент лежит внутри обёртки-контейнера, поэтому путь
            # задаётся явно от самого блока.
            for content in item.find_elements(
                    By.CSS_SELECTOR,
                    ":scope > .q-expansion-item__container "
                    "> .q-expansion-item__content"):
                if content.is_displayed() and content.size["height"] > 0 \
                        and content.text.strip():
                    return content
        return False

    content = wait(driver, opened, timeout, f"блок {label!r} не раскрылся")
    if appears:
        wait_text(driver, appears, timeout)
    return content


def select_option(driver, field_label, option_substring, timeout=TIMEOUT):
    """Выбрать значение в выпадающем списке Quasar.

    Кликать нужно по оболочке компонента, а не по полю ввода: внутри
    `q-select` лежит скрытый `input` размером 1×1 пиксель, который
    Selenium справедливо считает невидимым. Видимую часть рисует
    соседний элемент, и именно она принимает клик.
    """
    xpath = (f"//input[@aria-label={field_label!r}]"
             "/ancestor::*[contains(@class,'q-select')][1]")
    field = wait(driver, EC.element_to_be_clickable((By.XPATH, xpath)), timeout,
                 f"не найдено поле {field_label!r}")
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", field)
    field.click()
    items = wait(driver, lambda d: [
        i for i in d.find_elements(By.CSS_SELECTOR, ".q-menu .q-item")
        if option_substring in i.text] or False, timeout,
        f"в списке {field_label!r} нет варианта {option_substring!r}")
    items[0].click()
    wait(driver, lambda d: not d.find_elements(By.CSS_SELECTOR, ".q-menu"), timeout)
    return field


def fill(driver, field_label, value, timeout=TIMEOUT):
    """Ввод в текстовое поле. Здесь `input` видимый, в отличие от селекта."""
    field = wait(driver, EC.element_to_be_clickable(
        (By.CSS_SELECTOR, f"input[aria-label='{field_label}']")), timeout,
        f"не найдено поле {field_label!r}")
    field.clear()
    field.send_keys(value)
    return field


def field_value(driver, field_label):
    """Текущее значение поля — работает и для скрытого input селекта."""
    element = driver.find_element(By.CSS_SELECTOR, f"input[aria-label='{field_label}']")
    return element.get_attribute("value")


def demo_code(driver):
    """Текущий одноразовый код со страницы входа.

    На демонстрационном стенде код показывается прямо в форме — это
    предусмотрено для проверяющего. Тест пользуется тем же путём,
    что и человек, а не читает секрет из базы.
    """
    expand(driver, "Код для демонстрации")
    click_button(driver, "Показать")
    wait(driver, lambda d: re.search(r"текущий код:\s*\d{6}", d.page_source) or False,
         message="код для демонстрации не появился")
    return re.search(r"текущий код:\s*(\d{6})", driver.page_source).group(1)


def login(driver, base_url, role, attempts=3):
    """Полный вход через интерфейс: выбор пользователя, код, кнопка.

    Вход выполняется с повторами, и причина не в ненадёжности проверки.
    Значение поля уезжает на сервер по веб-сокету, а нажатие кнопки —
    отдельным сообщением; изредка клик обгоняет синхронизацию, и сервер
    видит пустой код. Лечить это паузой перед кликом нельзя: пауза либо
    мала и не спасает, либо велика и замедляет весь набор. Поэтому
    неудачная попытка распознаётся по сообщению на экране, и вход
    повторяется со свежим кодом.
    """
    full_name = ROLES[role]
    for attempt in range(attempts):
        driver.get(base_url.rstrip("/") + "/login")
        wait_text(driver, "одноразовый код")
        select_option(driver, "пользователь", full_name)
        fill(driver, "одноразовый код", demo_code(driver))
        click_button(driver, "Войти")
        try:
            wait(driver, lambda d: "Очередь на проверку" in d.page_source
                 or "неверный одноразовый код" in d.page_source,
                 timeout=10, message="форма входа не ответила")
        except TimeoutException:
            continue
        if "Очередь на проверку" in driver.page_source:
            wait_text(driver, full_name.split()[-1])
            return driver
    raise AssertionError(f"вход под ролью {role!r} не выполнен "
                         f"за {attempts} попытки")


def logout(driver, base_url):
    """Сброс сессии между тестами."""
    driver.get(base_url.rstrip("/") + "/login")
    try:
        driver.delete_all_cookies()
    except Exception:
        pass
    driver.get(base_url.rstrip("/") + "/login")


def count_text(driver, pattern):
    """Сколько раз встречается регулярное выражение в разметке страницы."""
    return len(re.findall(pattern, driver.page_source))


def canvases(driver):
    """Диаграммы ECharts рисуются в canvas — считаем их как признак отрисовки."""
    return [c for c in driver.find_elements(By.CSS_SELECTOR, "canvas") if c.is_displayed()]


def safe_quit(driver):
    try:
        driver.quit()
    except (TimeoutException, Exception):
        pass
