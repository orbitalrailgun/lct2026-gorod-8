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
