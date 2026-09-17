"""День 1: поведенческий тест каналов-тёзок и таблица логических доменов.

Два вопроса, на которые нельзя ответить из справочника, только из поведения:

1. Каналы-тёзки (9 % парка) — это пороговые каналы одного прибора или разные
   приборы с общим именем? Заказчик на форуме АВОК определил канал как
   «показание, верхний порог, нижний порог», но в данных это не помечено.
   Пороговые каналы одного прибора обязаны срабатывать синхронно, разные
   извещатели — нет.

2. Логический домен — группа каналов, способных подтвердить друг друга.
   Заказчик дал правило («если один даёт тревогу, а второй нет, тревоги нет»),
   но кто кому сосед, в данных не записано. Выводим из совместной активности
   внутри одной точки.

Запуск:  .venv/bin/python -m scripts.02_twins_and_domains
"""

from core import etl


def test_twins(con):
    """Синхронность тёзок: доля общих суток, в которые их триты совпадают."""
    con.execute("""
        CREATE OR REPLACE TABLE twin_sync AS
        WITH tw AS (
            SELECT c.twin_group, c.object_id, c.sensor_name, c.sensor_type,
                   c.channel_id, f.d, f.trit_day
            FROM dim_channel c
            JOIN fact_channel_day f ON f.channel_id = c.channel_id
            WHERE c.twin_count > 1
        ),
        pairs AS (
            SELECT a.twin_group, a.sensor_name, a.sensor_type,
                   a.channel_id AS ch_a, b.channel_id AS ch_b, a.d,
                   (a.trit_day = b.trit_day) AS same
            FROM tw a JOIN tw b
              ON a.twin_group = b.twin_group AND a.d = b.d AND a.channel_id < b.channel_id
        )
        SELECT twin_group, any_value(sensor_name) AS sensor_name,
               any_value(sensor_type) AS sensor_type,
               count(DISTINCT ch_a || '-' || ch_b) AS n_pairs,
               count(*) AS shared_days,
               avg(CAST(same AS INTEGER))::DOUBLE AS sync_rate
        FROM pairs GROUP BY twin_group
    """)
    return con.execute("SELECT count(*) FROM twin_sync").fetchone()[0]


def build_logical_domains(con):
    """Логические домены: каналы одной точки, способные подтверждать друг друга.

    Точка — пара (объект, пикет): заказчик прямо назвал пикет контейнером,
    внутри которого находятся датчики. Домен имеет смысл только там, где в точке
    есть хотя бы два канала, иначе подтверждать нечем — и это само по себе
    важный факт, который вердикт обязан сообщать.
    """
    con.execute("""
        CREATE OR REPLACE TABLE logical_domain AS
        SELECT
            object_id,
            picket,
            count(*)                                   AS n_channels,
            count(DISTINCT sensor_type)                AS n_sensor_types,
            count(DISTINCT system)                     AS n_systems,
            list(DISTINCT sensor_type)                 AS sensor_types,
            list(channel_id)                           AS channel_ids,
            any_value(object_name)                     AS object_name,
            any_value(parent_name)                     AS parent_name,
            -- можно ли в этой точке подтвердить задымление теплом
            bool_or(sensor_type IN ('Тепловой датчик', 'Датчик температуры')) AS has_thermal,
            bool_or(sensor_type = 'Датчик дыма')                              AS has_smoke,
            bool_or(sensor_type = 'Состояние насоса')                         AS has_pump,
            bool_or(sensor_type IN ('Датчик движения', 'КД Дверь', 'КД Люк')) AS has_intrusion
        FROM dim_channel
        WHERE picket IS NOT NULL AND NOT is_zone_aggregate
        GROUP BY object_id, picket
    """)
    return con.execute("SELECT count(*) FROM logical_domain").fetchone()[0]


def main():
    con = etl.connect()
    for m in ("dim_channel", "fact_channel_day", "channel_profile"):
        etl.load_mart(con, m)

    n = test_twins(con)
    print(f"групп тёзок с общими сутками: {n}", flush=True)
    etl.export(con, "twin_sync")

    print("\n=== СИНХРОННОСТЬ ТЁЗОК ===")
    con.sql("""
        SELECT CASE WHEN sync_rate >= 0.95 THEN 'синхронны (>=95%) — пороги одного прибора'
                    WHEN sync_rate >= 0.70 THEN 'скорее синхронны (70-95%)'
                    WHEN sync_rate >= 0.40 THEN 'слабо связаны (40-70%)'
                    ELSE 'независимы (<40%) — разные приборы' END AS вывод,
               count(*) AS групп, sum(n_pairs) AS пар_каналов,
               round(median(sync_rate), 3) AS медиана_синхронности
        FROM twin_sync GROUP BY 1 ORDER BY групп DESC
    """).show(max_width=140)

    n = build_logical_domains(con)
    print(f"\nлогических доменов (объект x пикет): {n}", flush=True)
    etl.export(con, "logical_domain")

    print("\n=== РАЗМЕР ДОМЕНОВ ===")
    con.sql("""
        SELECT CASE WHEN n_channels = 1 THEN '1 канал — подтверждать нечем'
                    WHEN n_channels <= 3 THEN '2-3 канала'
                    WHEN n_channels <= 10 THEN '4-10 каналов'
                    ELSE 'более 10' END AS размер,
               count(*) AS доменов, sum(n_channels) AS каналов
        FROM logical_domain GROUP BY 1 ORDER BY каналов DESC
    """).show()

    print("=== НАБЛЮДАЕМОСТЬ: где дым можно подтвердить теплом ===")
    con.sql("""
        SELECT has_smoke AS есть_дым, has_thermal AS есть_термика,
               count(*) AS доменов, sum(n_channels) AS каналов
        FROM logical_domain WHERE has_smoke GROUP BY 1,2 ORDER BY доменов DESC
    """).show()


if __name__ == "__main__":
    main()
