"""Аутентификация и разграничение доступа.

Ролевая модель повторяет оргструктуру, которую описал заказчик: техник видит
один объект, диспетчер района — группу объектов, диспетчер ОДС — всё предприятие.
Четвёртая роль, группа реагирования, добавлена по предложению ментора: она
подтверждает факт на объекте и тем самым замыкает цикл накопления разметки.

Второй фактор реализован через TOTP. Ментор назвал двухфакторную аутентификацию
примером требования, невыполнение которого без объяснения снимает решение
по формальному признаку, поэтому она сделана работающей, а не задекларированной.

Интеграция с LDAP/AD оставлена точкой расширения: подключение к каталогу
заказчика вне контура конкурса, но место подключения обозначено явно.
"""

import pyotp

from core import db

# Что доступно роли. Область видимости объектов задаётся отдельно таблицей
# user_object: у техника один объект, у диспетчера района несколько,
# у диспетчера ОДС ограничений нет.
ROLE_RIGHTS = {
    "техник": {
        "scope": "объект",
        "can_decide": False,
        "can_admin": False,
        "title": "Техник",
    },
    "диспетчер_района": {
        "scope": "район",
        "can_decide": True,
        "can_admin": False,
        "title": "Диспетчер района",
    },
    "диспетчер_одс": {
        "scope": "всё",
        "can_decide": True,
        "can_admin": True,
        "title": "Диспетчер ОДС",
    },
    "группа_реагирования": {
        "scope": "объект",
        "can_decide": True,
        "can_admin": False,
        "title": "Группа реагирования",
    },
}


def get_user(con, login):
    with con.cursor() as cur:
        cur.execute(
            "SELECT id, login, full_name, role, totp_secret FROM app_user WHERE login = %s",
            (login,))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "login": row[1], "full_name": row[2],
            "role": row[3], "totp_secret": row[4]}


def list_users(con):
    with con.cursor() as cur:
        cur.execute("SELECT login, full_name, role FROM app_user ORDER BY id")
        return [{"login": r[0], "full_name": r[1], "role": r[2]} for r in cur.fetchall()]


def verify_totp(secret, code):
    """Проверка одноразового кода. Окно в один шаг компенсирует расхождение часов."""
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)


def current_code(secret):
    """Текущий код — нужен только для демонстрации, в эксплуатации не используется."""
    return pyotp.TOTP(secret).now() if secret else None


def login(con, login_name, code):
    """Вход: логин плюс второй фактор. Результат пишется в журнал действий."""
    user = get_user(con, login_name)
    if not user:
        return None, "пользователь не найден"
    if not verify_totp(user["totp_secret"], code):
        db.log_action(con, None, "неудачный вход", "app_user", login_name)
        return None, "неверный одноразовый код"
    db.log_action(con, user["id"], "вход в систему", "app_user", login_name)
    return user, None


def rights(role):
    return ROLE_RIGHTS.get(role, ROLE_RIGHTS["техник"])


def visible_objects(con, user):
    """Список объектов, доступных пользователю. None означает «все»."""
    if rights(user["role"])["scope"] == "всё":
        return None
    with con.cursor() as cur:
        cur.execute("SELECT object_id FROM user_object WHERE user_id = %s", (user["id"],))
        rows = [r[0] for r in cur.fetchall()]
    return rows


def can_see(user, object_id, allowed):
    """Видит ли пользователь объект. Пустой список означает отсутствие назначений."""
    if allowed is None:
        return True
    return object_id in allowed


def assign_objects(con, user_id, object_ids):
    """Назначение объектов пользователю — область видимости для роли."""
    with con.cursor() as cur:
        cur.executemany(
            "INSERT INTO user_object (user_id, object_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            [(user_id, oid) for oid in object_ids])
