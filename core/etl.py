"""Построение витрин из сырого журнала.

Журнал — 313 546 016 событий. Полные сканы по нему делаются один раз здесь,
дальше всё считается по компактным витринам. Это не только скорость: машина
разработки имеет 16 ГБ ОЗУ, и параллельные процессы DuckDB с большим лимитом
её роняют, поэтому лимит задаётся явно при каждом подключении.
"""

import os

import duckdb

from core import config, contract


def connect(memory=None, threads=None):
    """Соединение с DuckDB под ограничения машины."""
    os.makedirs(config.DUCKDB_TMP, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{memory or config.DUCKDB_MEMORY}'")
    con.execute(f"SET threads={threads or config.DUCKDB_THREADS}")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{config.DUCKDB_TMP}'")
    return con


def register_sources(con):
    """Подключает сырые источники как представления."""
    con.execute(f"""
        CREATE OR REPLACE VIEW journal AS
        SELECT * FROM read_parquet('{config.PARQUET_DIR}/j*.parquet')
    """)
    # В справочнике каналов CRLF и переводы строк внутри поля названия:
    # 11 517 физических строк против 11 485 логических записей. Наивное чтение
    # потеряет тип у 31 канала, поэтому параметры задаются явно и проверяются.
    con.execute(f"""
        CREATE OR REPLACE VIEW ch_raw AS
        SELECT * FROM read_csv('{config.CH_CSV}', header=true, quote='"', strict_mode=false)
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW ob_raw AS
        SELECT * FROM read_csv('{config.OB_CSV}', header=true)
    """)
    n = con.execute("SELECT count(*) FROM ch_raw").fetchone()[0]
    if n != 11485:
        raise RuntimeError(
            f"справочник каналов прочитан как {n} строк вместо 11485 — "
            "проверьте quotechar и переводы строк внутри полей"
        )
    return con


def build_dim_channel(con):
    """Измерение каналов: паспорт, локация, признаки пригодности.

    Локация берётся из официального ид_объект (появился в обновлении 16.09.2026,
    покрытие 96,99 % событий), пикет — из названия датчика, где он есть у 92 %.
    Тег как адрес не используется: заказчик назвал его избыточным, это MQTT-топик.
    Но база тега сохраняется как признак группировки шлейфа — сегментные отказы
    доказаны данными, а физика подтверждена: одна питающая линия 48 В кормит
    десятки датчиков, и её обрыв роняет их все разом.
    """
    zone_cond = " OR ".join(
        f"lower(c.название_датчика) LIKE '%{m.lower()}%'" for m in contract.ZONE_AGGREGATE_MARKERS
    )
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_channel AS
        WITH base AS (
            SELECT
                c.ид_канала_данных                                   AS channel_id,
                c.тип_инж_системы                                    AS system,
                c.тип_датчика                                        AS sensor_type,
                c.название_датчика                                   AS sensor_name,
                c.тег_инженерной_системы                             AS tag,
                c.ид_объект                                          AS object_id,
                split_part(c.тег_инженерной_системы, '-', 1)         AS tag_base,
                TRY_CAST(regexp_extract(c.название_датчика, 'ПК([0-9]+)', 1) AS INTEGER) AS picket,
                o.диспетчерское_название_объекта                     AS object_name,
                o.вид_объекта                                        AS object_kind,
                o.родитель                                           AS parent_id,
                p.диспетчерское_название_объекта                     AS parent_name,
                ({zone_cond})                                        AS is_zone_aggregate,
                (c.тип_датчика IN {contract.POWER_TYPES})             AS is_power_type
            FROM ch_raw c
            LEFT JOIN ob_raw o ON c.ид_объект = o.ид_объект
            LEFT JOIN ob_raw p ON o.родитель  = p.ид_объект
        )
        SELECT
            *,
            -- Каналы-тёзки: неразличимы для диспетчера по названию внутри объекта.
            -- Природа дублей разная (пороги одного прибора либо несколько приборов
            -- с общим именем), но схлопывать ленту надо в любом случае.
            count(*) OVER (PARTITION BY object_id, sensor_name, sensor_type) AS twin_count,
            dense_rank() OVER (ORDER BY object_id, sensor_name, sensor_type)  AS twin_group
        FROM base
    """)
    return con.execute("SELECT count(*) FROM dim_channel").fetchone()[0]


def build_dim_value_class(con):
    """Справочник «тип датчика + значение → класс» как таблица.

    Именно таблица, а не CASE-выражение: на 313 млн строк перебор 199 строковых
    условий стоит сотни сравнений на каждую строку, а join по маленькой таблице
    DuckDB делает одним хеш-проходом. Источник тот же CSV, расхождение исключено.
    """
    table = contract.load_contract()
    rows = [(t, v, c) for (t, v), c in table.items()]
    con.execute("CREATE OR REPLACE TABLE dim_value_class (sensor_type VARCHAR, raw_value VARCHAR, cls VARCHAR)")
    con.executemany("INSERT INTO dim_value_class VALUES (?, ?, ?)", rows)
    return len(rows)


def build_fact_channel_day(con):
    """Канало-сутки: счётчики по классам контракта и трит дня.

    Трит дня — эскалация по событиям: если за сутки было хоть одно отклонение,
    сутки отклонённые. Неизвестность не затирает отклонение, но и не превращается
    в норму, поэтому берётся max по порядку -1 < 0 < +1.
    """
    trit = contract.build_sql_trit("cls")
    low, high = config.TEMP_VALID_RANGE
    sentinels = ", ".join(str(v) for v in config.TEMP_SENTINELS)
    num = "TRY_CAST(replace(j.raw_value, ',', '.') AS DOUBLE)"

    con.execute(f"""
        CREATE OR REPLACE TABLE fact_channel_day AS
        WITH ev AS (
            SELECT
                j.channel_id, j.d, j.t, j.is_alarm,
                -- валидное измерение: сентинелы и выбросы обнуляются здесь,
                -- чтобы не попасть ни в среднее, ни в min/max
                CASE WHEN {num} IS NOT NULL
                       AND {num} NOT IN ({sentinels})
                       AND {num} BETWEEN {low} AND {high}
                     THEN {num} END AS num_ok,
                COALESCE(
                    vc.cls,
                    CASE
                      WHEN j.raw_value LIKE '{config.EPOCH_GARBAGE_PREFIX}%' THEN '{contract.GARBAGE}'
                      WHEN {num} IS NOT NULL
                           AND {num} NOT IN ({sentinels})
                           AND {num} BETWEEN {low} AND {high} THEN '{contract.NUMERIC}'
                      ELSE '{contract.GARBAGE}'
                    END
                ) AS cls
            FROM journal j
            LEFT JOIN dim_channel d ON j.channel_id = d.channel_id
            LEFT JOIN dim_value_class vc
                   ON vc.sensor_type = d.sensor_type AND vc.raw_value = j.raw_value
        ), tr AS (
            SELECT *, {trit} AS trit FROM ev
        )
        SELECT
            channel_id, d,
            count(*)                                            AS n_ev,
            sum(CASE WHEN is_alarm THEN 1 ELSE 0 END)           AS n_alarm,
            max(trit)                                           AS trit_day,
            count(*) FILTER (WHERE cls = '{contract.NORM}')     AS n_norm,
            count(*) FILTER (WHERE cls = '{contract.TRIGGER}')  AS n_trigger,
            count(*) FILTER (WHERE cls = '{contract.FAULT}')    AS n_fault,
            count(*) FILTER (WHERE cls = '{contract.DISABLED}') AS n_disabled,
            count(*) FILTER (WHERE cls = '{contract.UNDEFINED}')AS n_undefined,
            count(*) FILTER (WHERE cls = '{contract.GARBAGE}')  AS n_garbage,
            count(*) FILTER (WHERE cls = '{contract.NUMERIC}')  AS n_numeric,
            -- числовые агрегаты считаются только по валидным измерениям:
            -- сентинел -127 рядом с +18 даёт мнимый скачок на 145 градусов
            avg(num_ok) AS num_avg,
            min(num_ok) AS num_min,
            max(num_ok) AS num_max,
            count(num_ok) AS n_num_ok,
            min(t) AS t_first, max(t) AS t_last,
            -- Метки по классам: данные хранят время с точностью до секунды,
            -- поэтому вердикт может назвать не только сутки, но и момент.
            -- Разница между «признаки появились сегодня» и «сегодня в 03:12»
            -- для диспетчера существенна: ночное развитие читается иначе.
            min(t) FILTER (WHERE cls IN ('{contract.FAULT}', '{contract.DISABLED}')) AS t_first_fault,
            max(t) FILTER (WHERE cls IN ('{contract.FAULT}', '{contract.DISABLED}')) AS t_last_fault,
            min(t) FILTER (WHERE cls = '{contract.TRIGGER}')   AS t_first_trigger,
            min(t) FILTER (WHERE cls = '{contract.UNDEFINED}') AS t_first_undefined
        FROM tr
        GROUP BY channel_id, d
    """)
    return con.execute("SELECT count(*) FROM fact_channel_day").fetchone()[0]


def build_fact_object_day(con):
    """Объекто-сутки: агрегат для дашборда и для признаков уровня объекта.

    Наблюдаемость считается явно: доля каналов объекта, про которые в эти сутки
    вообще что-то известно. Вердикт по объекту, где наблюдаемы 2 канала из 200,
    и вердикт по полностью наблюдаемому объекту — разные по весу, и диспетчер
    должен это видеть.
    """
    con.execute("""
        CREATE OR REPLACE TABLE fact_object_day AS
        SELECT
            c.object_id, c.parent_id, f.d,
            count(DISTINCT f.channel_id)                                     AS n_channels_active,
            sum(f.n_ev)                                                      AS n_ev,
            sum(f.n_alarm)                                                   AS n_alarm,
            sum(f.n_fault + f.n_disabled)                                    AS n_failure_ev,
            count(DISTINCT f.channel_id) FILTER (WHERE f.trit_day = 1)       AS n_ch_deviation,
            count(DISTINCT f.channel_id) FILTER (WHERE f.trit_day = 0)       AS n_ch_unknown,
            count(DISTINCT f.channel_id) FILTER (WHERE f.trit_day = -1)      AS n_ch_normal,
            max(f.trit_day)                                                  AS trit_day
        FROM fact_channel_day f
        JOIN dim_channel c ON f.channel_id = c.channel_id
        GROUP BY c.object_id, c.parent_id, f.d
    """)
    return con.execute("SELECT count(*) FROM fact_object_day").fetchone()[0]


def export(con, table, name=None):
    """Сохраняет витрину в Parquet."""
    os.makedirs(config.MART_DIR, exist_ok=True)
    path = os.path.join(config.MART_DIR, f"{name or table}.parquet")
    con.execute(f"COPY {table} TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    return path


def load_mart(con, name):
    """Подключает готовую витрину как представление."""
    path = os.path.join(config.MART_DIR, f"{name}.parquet")
    con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return con


def build_channel_profile(con):
    """Профиль активности канала: что для него нормально.

    Журнал событийный — канал пишет при смене состояния, а не по расписанию.
    Медиана всего 2 события в сутки, и лишь 13,6 % возможных канало-суток вообще
    содержат хоть одну запись. Поэтому отсутствие строки нельзя считать нормой:
    для болтливого канала суточное молчание — признак отказа, для молчаливого —
    обычное дело. Профиль даёт ту личную норму, относительно которой это решается.
    """
    con.execute("""
        CREATE OR REPLACE TABLE channel_profile AS
        WITH span AS (
            SELECT channel_id,
                   min(d) AS first_day, max(d) AS last_day,
                   count(*) AS active_days,
                   sum(n_ev) AS total_ev,
                   median(n_ev) AS median_ev_per_active_day,
                   quantile_cont(n_ev, 0.95) AS p95_ev_per_active_day
            FROM fact_channel_day GROUP BY channel_id
        )
        SELECT
            s.*,
            date_diff('day', s.first_day, s.last_day) + 1           AS lifespan_days,
            active_days * 1.0 / (date_diff('day', s.first_day, s.last_day) + 1) AS active_share,
            -- типичный интервал между активными сутками: во сколько раз молчание
            -- должно его превысить, чтобы считаться отказом, задаёт trits.py
            (date_diff('day', s.first_day, s.last_day) + 1) * 1.0
                / nullif(s.active_days, 0)                          AS mean_gap_days
        FROM span s
    """)
    return con.execute("SELECT count(*) FROM channel_profile").fetchone()[0]
