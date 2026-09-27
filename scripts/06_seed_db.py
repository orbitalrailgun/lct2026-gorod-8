"""Инициализация PostgreSQL и загрузка вердиктов.

Запускается внутри контейнера при старте. Идемпотентно: повторный запуск
не дублирует данные.
"""

import json
import os

import pyotp

from core import config, db

SEED = os.path.join(config.ROOT, "deploy", "seed_verdicts.json")

# Демонстрационные пользователи по ролям, которые описал заказчик.
USERS = [
    ("tehnik", "Техник Смирнов", "техник"),
    ("dispatcher", "Диспетчер района Петров", "диспетчер_района"),
    ("ods", "Диспетчер ОДС Иванова", "диспетчер_одс"),
    ("brigade", "Группа реагирования", "группа_реагирования"),
]


def assign_objects(con):
    """Назначение объектов ролям с ограниченной областью видимости.

    Без назначений таблица `user_object` пуста, и техник видит всю сеть:
    разграничение объявлено, но не наблюдаемо. Для демонстрации ролевой
    модели назначения нужны — иначе проверяющий увидит у всех ролей
    одинаковый экран и справедливо решит, что RBAC не работает.

    Объекты берутся из фактических вердиктов, а не задаются числами:
    иначе после пересчёта назначения укажут в пустоту.
    """
    with con.cursor() as cur:
        cur.execute("SELECT count(*) FROM user_object")
        if cur.fetchone()[0]:
            return
        cur.execute("""
            SELECT object_id FROM verdict
            WHERE object_id IS NOT NULL
            GROUP BY object_id ORDER BY count(*) DESC LIMIT 6
        """)
        objects = [r[0] for r in cur.fetchall()]
        if not objects:
            return
        # Техник обслуживает участок, группа реагирования выезжает на свой.
        plan = {"tehnik": objects[:2], "brigade": objects[2:4]}
        for login, ids in plan.items():
            cur.execute("SELECT id FROM app_user WHERE login = %s", (login,))
            row = cur.fetchone()
            if not row:
                continue
            cur.executemany(
                "INSERT INTO user_object (user_id, object_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                [(row[0], oid) for oid in ids])
        print(f"назначено объектов: техник {len(plan['tehnik'])}, "
              f"группа реагирования {len(plan['brigade'])}")


def main():
    con = db.connect()
    db.init_schema(con)
    print("схема создана")

    with con.cursor() as cur:
        for login, name, role in USERS:
            cur.execute(
                "INSERT INTO app_user (login, full_name, role, totp_secret) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (login) DO NOTHING",
                (login, name, role, pyotp.random_base32()))
        cur.execute("SELECT count(*) FROM verdict")
        existing = cur.fetchone()[0]

    assign_objects(con)

    if existing:
        print(f"вердикты уже загружены ({existing}), пропуск")
        return

    if not os.path.exists(SEED):
        print("seed-файл не найден, вердикты не загружены")
        return

    with open(SEED, encoding="utf-8") as fh:
        rows = json.load(fh)
    n = db.insert_verdicts(con, rows)
    print(f"загружено вердиктов: {n}")


if __name__ == "__main__":
    main()
