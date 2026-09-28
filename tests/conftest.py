"""Общие фикстуры тестов.

Тесты делятся на два вида, и это разделение намеренное.

Первые не требуют ничего: они проверяют чистую логику — согласование
числительных, троичную алгебру, семантический контракт, форматы обмена.
Такие тесты выполняются за доли секунды и ловят большинство ошибок.

Вторые работают против поднятого стенда через настоящий браузер, потому
что интерфейс собран на веб-сокетах: проверить его запросом к HTTP нельзя,
половина содержимого дособирается после загрузки, а раскрывающиеся блоки
наполняются только по клику. Если стенд не поднят, такие тесты пропускаются
с внятным сообщением, а не падают.

Запуск:

    pytest tests                    # всё
    pytest tests -m "not ui"        # только логика, без браузера
    pytest tests -m mutating        # только меняющие состояние

Переменные окружения:

    CONCORDE_URL     адрес стенда, по умолчанию http://localhost:8080
    CONCORDE_HEADED  показывать браузер (для отладки)
"""

import os
import socket
import urllib.request
from urllib.parse import urlparse

import pytest

from tests import ui

BASE_URL = os.environ.get("CONCORDE_URL", "http://localhost:8080")


def pytest_configure(config):
    config.addinivalue_line("markers", "ui: тест работает через браузер")
    config.addinivalue_line("markers", "mutating: тест меняет данные стенда")


def stand_is_up(url, timeout=3):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            return r.status == 200
    except (OSError, socket.timeout):
        return False


@pytest.fixture(scope="session")
def base_url():
    if not stand_is_up(BASE_URL):
        pytest.skip(f"стенд не отвечает по адресу {BASE_URL}: "
                    "поднимите его командой `docker compose up`")
    return BASE_URL


@pytest.fixture(scope="session")
def browser(base_url):
    """Один браузер на весь прогон: запуск занимает секунды, а тестов много."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    if not os.environ.get("CONCORDE_HEADED"):
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1500,1100")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Локаль фиксируется: часть проверок сверяет русский текст интерфейса.
    options.add_argument("--lang=ru-RU")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    yield driver
    ui.safe_quit(driver)


@pytest.fixture
def anonymous(browser, base_url):
    """Чистая сессия без входа."""
    ui.logout(browser, base_url)
    return browser


@pytest.fixture
def as_role(browser, base_url):
    """Вход под заданной ролью. Возвращает функцию, чтобы тест выбирал сам."""
    def _login(role):
        ui.logout(browser, base_url)
        return ui.login(browser, base_url, role)
    return _login


@pytest.fixture
def dispatcher(as_role):
    return as_role("диспетчер_района")


@pytest.fixture
def ods(as_role):
    return as_role("диспетчер_одс")


@pytest.fixture
def technician(as_role):
    return as_role("техник")


@pytest.fixture(scope="session")
def api(base_url):
    """Простой клиент HTTP: тесты API браузера не требуют."""
    def _get(path, headers=None):
        request = urllib.request.Request(base_url.rstrip("/") + path,
                                         headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)
    return _get


def reset_stand():
    """Вернуть стенд к исходному набору вердиктов.

    Сценарные тесты закрывают вердикты и создают заявки — иначе замкнутый
    цикл не проверить. Без возврата в исходное состояние второй прогон
    работал бы с уже отработанными карточками и падал бы не потому,
    что в приложении ошибка.

    Используются те же две команды, что описаны в README и в записке.
    Если docker недоступен (стенд поднят иначе), возвращается False,
    и тесты подстраиваются под текущее состояние.
    """
    import subprocess
    commands = (
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "concorde",
         "-c", "TRUNCATE verdict CASCADE;"],
        ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.06_seed_db"],
    )
    for command in commands:
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            return False
        if done.returncode != 0:
            return False
    return True


@pytest.fixture(scope="module")
def fresh_verdicts(base_url):
    """Исходный набор вердиктов перед сценарными тестами."""
    if os.environ.get("CONCORDE_NO_RESET"):
        return False
    return reset_stand()


@pytest.fixture(scope="session")
def host_port(base_url):
    parsed = urlparse(base_url)
    return parsed.hostname, parsed.port or 80
