"""Оперативный контур в PostgreSQL.

Разделение хранилищ намеренное. Аналитика и признаки живут в DuckDB и Parquet:
313 млн событий там сканируются за секунды. В PostgreSQL лежит то, что меняется
в работе диспетчера и требует транзакций — вердикты, решения, заявки, пользователи,
аудит. Требование ТЗ «PostgreSQL 12 и выше» закрывается именно этим контуром.

Справочник причин закрытия — не наша выдумка: заказчик описал такой справочник
в своей системе мониторинга («ложная сработка, проверка, профилактика, выезд,
испытания»), но выгружать его отказался. Мы воспроизводим его у себя, и это
одновременно закрывает требование ТЗ о фиксации решения диспетчера и создаёт
механизм накопления разметки, которой сейчас нет ни у нас, ни у заказчика.
"""

import os

import psycopg

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://concorde:concorde@localhost:5432/concorde"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
    id          SERIAL PRIMARY KEY,
    login       TEXT UNIQUE NOT NULL,
    full_name   TEXT NOT NULL,
    role        TEXT NOT NULL,
    totp_secret TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Роли повторяют оргструктуру, которую описал заказчик: техник видит один объект,
-- диспетчер района — группу, диспетчер ОДС — всё предприятие. Группа реагирования
-- добавлена по предложению ментора: она подтверждает факт на объекте и тем самым
-- замыкает цикл накопления разметки.
CREATE TABLE IF NOT EXISTS role_scope (
    role        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    can_decide  BOOLEAN NOT NULL DEFAULT FALSE,
    can_admin   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS user_object (
    user_id     INTEGER REFERENCES app_user(id) ON DELETE CASCADE,
    object_id   INTEGER NOT NULL,
    PRIMARY KEY (user_id, object_id)
);

CREATE TABLE IF NOT EXISTS verdict (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    scenario      TEXT NOT NULL,              -- отказ | подтопление | пожар | проникновение
    object_id     INTEGER,
    object_name   TEXT,
    picket        INTEGER,
    channel_id    BIGINT,
    probability   DOUBLE PRECISION NOT NULL,
    horizon_hours INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    card          JSONB NOT NULL,             -- карточка целиком: улики, слепые зоны, контрфакт
    -- Квитирование: вердикт остаётся неотработанным, пока диспетчер его не закрыл.
    -- Термин и механика взяты у заказчика: «пока человек не отработает событие,
    -- оно не пропадёт из оперативного журнала».
    status        TEXT NOT NULL DEFAULT 'новый',
    acked_by      INTEGER REFERENCES app_user(id),
    acked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS verdict_status_idx  ON verdict (status, created_at DESC);
CREATE INDEX IF NOT EXISTS verdict_object_idx  ON verdict (object_id, created_at DESC);
CREATE INDEX IF NOT EXISTS verdict_scenario_idx ON verdict (scenario, created_at DESC);

CREATE TABLE IF NOT EXISTS reason_ref (
    id        SERIAL PRIMARY KEY,
    code      TEXT UNIQUE NOT NULL,
    title     TEXT NOT NULL,
    is_false  BOOLEAN NOT NULL DEFAULT FALSE  -- считается ли ложным срабатыванием
);

CREATE TABLE IF NOT EXISTS decision (
    id          BIGSERIAL PRIMARY KEY,
    verdict_id  BIGINT NOT NULL REFERENCES verdict(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES app_user(id),
    reason_id   INTEGER REFERENCES reason_ref(id),
    action      TEXT NOT NULL,                -- выезд | мониторинг | без выезда
    comment     TEXT,
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS decision_verdict_idx ON decision (verdict_id);

CREATE TABLE IF NOT EXISTS setting (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    title       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Черновик заявки на ремонт. Реальная отправка во внешнюю систему не требуется:
-- журнал ОДС ведётся отдельно и интегрирован не будет. Заявка формируется здесь
-- и выгружается в XLSX, а обоснование переносится из карточки вердикта целиком,
-- чтобы документ не расходился с данными, на которых построен прогноз.
CREATE TABLE IF NOT EXISTS work_order_draft (
    id             BIGSERIAL PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    verdict_id     BIGINT REFERENCES verdict(id) ON DELETE SET NULL,
    author_id      INTEGER REFERENCES app_user(id),
    object_name    TEXT,
    picket         INTEGER,
    work_type      TEXT NOT NULL,
    priority       TEXT NOT NULL,
    due_date       DATE,
    responsible    TEXT,
    justification  TEXT,
    recommendation TEXT,
    status         TEXT NOT NULL DEFAULT 'черновик'
);

CREATE INDEX IF NOT EXISTS work_order_verdict_idx ON work_order_draft (verdict_id);
CREATE INDEX IF NOT EXISTS work_order_due_idx     ON work_order_draft (due_date);

-- Требование ТЗ: журналирование всех действий пользователей.
CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id    INTEGER,
    action     TEXT NOT NULL,
    entity     TEXT,
    entity_id  TEXT,
    details    JSONB
);
"""

ROLES = [
    ("техник", "Техник (один объект)", False, False),
    ("диспетчер_района", "Диспетчер района", True, False),
    ("диспетчер_одс", "Диспетчер ОДС (всё предприятие)", True, True),
    ("группа_реагирования", "Группа реагирования", True, False),
]

# Причины закрытия по номенклатуре, которую заказчик назвал на встрече.
REASONS = [
    ("ложная_сработка", "Ложное срабатывание", True),
    ("профилактика", "Профилактика", True),
    ("проверка", "Плановая проверка", True),
    ("испытания", "Технологические испытания", True),
    ("подтверждено", "Неисправность подтверждена", False),
    ("выезд", "Выезд бригады", False),
    ("в_работе", "Передано в ремонт", False),
]

DEFAULT_SETTINGS = [
    ("horizon_hours", "24", "Горизонт прогноза, часов"),
    ("target_precision", "0.70", "Целевая точность (Precision)"),
    ("target_recall", "0.50", "Целевая полнота (Recall)"),
    ("queue_size", "100", "Размер суточной очереди диспетчера"),
    ("show_toponyms", "0", "Показывать реальные названия объектов"),
]


def connect(dsn=None):
    """Соединение с PostgreSQL."""
    return psycopg.connect(dsn or DSN, autocommit=True)


def init_schema(con):
    """Создаёт схему и наполняет справочники. Идемпотентно."""
    with con.cursor() as cur:
        cur.execute(SCHEMA)
        cur.executemany(
            "INSERT INTO role_scope (role, title, can_decide, can_admin) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (role) DO NOTHING", ROLES)
        cur.executemany(
            "INSERT INTO reason_ref (code, title, is_false) VALUES (%s,%s,%s) "
            "ON CONFLICT (code) DO NOTHING", REASONS)
        cur.executemany(
            "INSERT INTO setting (key, value, title) VALUES (%s,%s,%s) "
            "ON CONFLICT (key) DO NOTHING", DEFAULT_SETTINGS)


def get_setting(con, key, default=None):
    with con.cursor() as cur:
        cur.execute("SELECT value FROM setting WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row else default


def set_setting(con, key, value, user_id=None):
    with con.cursor() as cur:
        cur.execute(
            "UPDATE setting SET value = %s, updated_at = now() WHERE key = %s", (value, key))
    log_action(con, user_id, "изменена настройка", "setting", key, {"value": value})


def log_action(con, user_id, action, entity=None, entity_id=None, details=None):
    """Запись в журнал действий — требование ТЗ по журналированию."""
    import json
    with con.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (user_id, action, entity, entity_id, details) "
            "VALUES (%s,%s,%s,%s,%s)",
            (user_id, action, entity, str(entity_id) if entity_id else None,
             json.dumps(details, ensure_ascii=False) if details else None))


def insert_verdicts(con, rows):
    """Пакетная запись вердиктов."""
    import json
    with con.cursor() as cur:
        cur.executemany(
            """INSERT INTO verdict
               (scenario, object_id, object_name, picket, channel_id,
                probability, horizon_hours, model_version, card)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [(r["scenario"], r.get("object_id"), r.get("object_name"), r.get("picket"),
              r.get("channel_id"), r["probability"], r["horizon_hours"],
              r["model_version"], json.dumps(r["card"], ensure_ascii=False))
             for r in rows])
    return len(rows)


def fetch_verdicts(con, status=None, scenario=None, limit=100):
    """Лента вердиктов для дашборда."""
    where, params = [], []
    if status:
        where.append("status = %s")
        params.append(status)
    if scenario:
        where.append("scenario = %s")
        params.append(scenario)
    # object_id в выборке обязателен: по нему работает разграничение
    # области видимости. Без него фильтр роли сравнивал None с назначениями
    # и молча оставлял технику пустой экран.
    sql = "SELECT id, created_at, scenario, object_id, object_name, picket, " \
          "channel_id, probability, horizon_hours, status, card FROM verdict"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY probability DESC, created_at DESC LIMIT %s"
    params.append(limit)
    with con.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def acknowledge(con, verdict_id, user_id):
    """Квитирование: вердикт взят в работу и перестаёт мигать в ленте."""
    with con.cursor() as cur:
        cur.execute(
            "UPDATE verdict SET status = 'в работе', acked_by = %s, acked_at = now() "
            "WHERE id = %s AND status = 'новый'", (user_id, verdict_id))
        changed = cur.rowcount
    log_action(con, user_id, "квитирование", "verdict", verdict_id)
    return changed


def record_decision(con, verdict_id, user_id, action, reason_code, comment=None):
    """Фиксация решения диспетчера — то, из чего со временем вырастет разметка."""
    with con.cursor() as cur:
        cur.execute("SELECT id FROM reason_ref WHERE code = %s", (reason_code,))
        row = cur.fetchone()
        reason_id = row[0] if row else None
        cur.execute(
            "INSERT INTO decision (verdict_id, user_id, reason_id, action, comment) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (verdict_id, user_id, reason_id, action, comment))
        decision_id = cur.fetchone()[0]
        cur.execute("UPDATE verdict SET status = 'закрыт' WHERE id = %s", (verdict_id,))
    log_action(con, user_id, "решение", "verdict", verdict_id,
               {"action": action, "reason": reason_code})
    return decision_id


def create_work_order(con, draft, user_id=None):
    """Сохраняет черновик заявки и пишет действие в журнал."""
    with con.cursor() as cur:
        cur.execute(
            """INSERT INTO work_order_draft
               (verdict_id, author_id, object_name, picket, work_type, priority,
                due_date, responsible, justification, recommendation)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (draft.get("verdict_id"), user_id, draft.get("object_name"),
             draft.get("picket"), draft["work_type"], draft["priority"],
             draft.get("due_date"), draft.get("responsible"),
             draft.get("justification"), draft.get("recommendation")))
        order_id = cur.fetchone()[0]
    log_action(con, user_id, "черновик заявки", "work_order_draft", order_id,
               {"verdict_id": draft.get("verdict_id"), "priority": draft["priority"]})
    return order_id


def fetch_work_orders(con, limit=200):
    """Список черновиков заявок для страницы и выгрузки."""
    with con.cursor() as cur:
        cur.execute("""
            SELECT w.id, w.created_at, w.object_name, w.picket, w.work_type,
                   w.priority, w.due_date, w.responsible, w.status,
                   w.justification, w.recommendation, u.full_name, w.verdict_id
            FROM work_order_draft w
            LEFT JOIN app_user u ON u.id = w.author_id
            ORDER BY w.due_date NULLS LAST, w.created_at DESC
            LIMIT %s
        """, (limit,))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def work_order_exists(con, verdict_id):
    """Есть ли уже заявка по этому вердикту — чтобы не плодить дубли."""
    with con.cursor() as cur:
        cur.execute("SELECT id FROM work_order_draft WHERE verdict_id = %s LIMIT 1",
                    (verdict_id,))
        row = cur.fetchone()
    return row[0] if row else None
