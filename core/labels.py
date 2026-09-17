"""Целевые переменные.

Главная — M1 «отказ канала в ближайшие 24 часа». Это единственная задача проекта,
где разметка настоящая: 1 750 304 события класса ОТКАЗ плюс 105 005 ОТКЛЮЧЕНО.
Остальные сценарии размечены только косвенно, и мы это честно заявляем.

Ключевые решения по таргету и их основания:

* «Неопределен» в таргет НЕ входит. Как предиктор жёсткого отказа он даёт lift
  1,28x против фона — почти неотличимо от случайности. Это потеря связи АРМ,
  а не поломка прибора.

* Энергетические типы (состояние фазы, насоса, вентилятора, ИБП, охраны)
  исключены. Они дают 76,9 % всех позитивов, причём у «Состояния фазы» 107 каналов
  из 957 проводят в «Обесточен» больше половины жизни — это штатный режим
  переключения вводов, а не отказ датчика.

* Массовые базо-дни исключены из позитивов. 86,19 % отказных канало-суток
  приходятся на дни, когда в отказ уходят десятки каналов одной базы разом,
  85 % из них в рабочие часы будня. Это следы работы бригад и обрывов питающей
  линии, а не независимые отказы приборов. Контроль качества фильтра: доля
  выходных среди оставшихся позитивов должна приблизиться к 28,6 %.

* Период внедрения СМВУ-2 (апрель–июнь 2021) и сутки обрыва выгрузки исключены
  целиком — и из позитивов, и из отрицательных примеров.
"""

from core import config, contract


def horizon_days(hours=None):
    """Горизонт прогноза в сутках. ТЗ требует не менее 24 часов."""
    return max(1, int((hours or config.HORIZON_HOURS) / 24))


def build_sweep_days(con):
    """Массовые базо-дни: работы на объекте или обрыв луча, а не отказ прибора.

    Признак формы, а не календаря: график ППР заказчик не ведёт, поэтому
    определяем по данным — много каналов одной базы в отказе за одни сутки.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE sweep_day AS
        WITH per_base AS (
            SELECT c.tag_base, f.d,
                   count(DISTINCT f.channel_id) FILTER (WHERE f.n_fault + f.n_disabled > 0) AS n_failed,
                   count(DISTINCT f.channel_id)                                             AS n_active
            FROM fact_channel_day f
            JOIN dim_channel c ON f.channel_id = c.channel_id
            GROUP BY c.tag_base, f.d
        ),
        base_size AS (
            SELECT tag_base, count(*) AS n_total FROM dim_channel GROUP BY tag_base
        )
        SELECT p.tag_base, p.d, p.n_failed, b.n_total,
               p.n_failed * 1.0 / nullif(b.n_total, 0) AS share_failed
        FROM per_base p JOIN base_size b USING (tag_base)
        WHERE p.n_failed >= {config.SWEEP_MIN_CHANNELS}
           OR p.n_failed * 1.0 / nullif(b.n_total, 0) >= {config.SWEEP_MIN_SHARE}
    """)
    return con.execute("SELECT count(*) FROM sweep_day").fetchone()[0]


def build_labels(con, hours=None):
    """Метка «отказ канала в горизонте» на плотном календаре.

    Плотный календарь обязателен: журнал событийный, заполнено лишь 13,6 %
    канало-суток. Считать долю позитивов по одним только активным суткам —
    значит завысить её в семь раз и обучить модель на несуществующем балансе.
    """
    h = horizon_days(hours)
    artifacts = " OR ".join(
        f"(cal.d BETWEEN DATE '{a}' AND DATE '{b}')" for a, b, _ in config.ARTIFACT_PERIODS
    )
    blackout = ", ".join(f"DATE '{d}'" for d in config.BLACKOUT_DATES)

    con.execute(f"""
        CREATE OR REPLACE TABLE labels AS
        WITH bounds AS (
            SELECT min(d) AS d0, max(d) AS d1 FROM fact_channel_day
        ),
        calendar AS (
            SELECT UNNEST(generate_series(
                (SELECT d0 FROM bounds), (SELECT d1 FROM bounds), INTERVAL 1 DAY
            ))::DATE AS d
        ),
        -- канал попадает в календарь только с момента ввода в эксплуатацию:
        -- 11 насосов подключены лишь в 2025, до этого их «молчание» ничего не значит
        -- Тёзки схлопываются до одного представителя группы, а не выбрасываются:
        -- их синхронность измерена и равна 1.000, то есть они дублируют друг друга,
        -- но сама точка наблюдения остаётся валидной и нужна в выборке.
        twin_rep AS (
            SELECT twin_group, min(channel_id) AS channel_id
            FROM dim_channel GROUP BY twin_group
        ),
        alive AS (
            SELECT c.channel_id, p.first_day, p.last_day
            FROM dim_channel c
            JOIN channel_profile p ON p.channel_id = c.channel_id
            JOIN twin_rep r ON r.channel_id = c.channel_id
            WHERE NOT c.is_zone_aggregate          -- агрегаты зон дают утечку таргета
              AND NOT c.is_power_type              -- энергетика — отдельная задача
        ),
        grid AS (
            SELECT a.channel_id, cal.d
            FROM alive a
            CROSS JOIN calendar cal
            WHERE cal.d BETWEEN a.first_day AND a.last_day
              AND NOT ({artifacts})
              AND cal.d NOT IN ({blackout})
        ),
        fail_day AS (
            SELECT f.channel_id, f.d,
                   (f.n_fault + f.n_disabled) > 0 AS is_fail
            FROM fact_channel_day f
        )
        SELECT
            g.channel_id,
            g.d,
            -- отказ в горизонте, исключая массовые дни: они про бригаду, не про прибор
            COALESCE(MAX(CASE WHEN fd.is_fail AND s.d IS NULL THEN 1 ELSE 0 END), 0) AS y
        FROM grid g
        LEFT JOIN fail_day fd
               ON fd.channel_id = g.channel_id
              AND fd.d > g.d AND fd.d <= g.d + {h}
        LEFT JOIN dim_channel c ON c.channel_id = g.channel_id
        LEFT JOIN sweep_day s ON s.tag_base = c.tag_base AND s.d = fd.d
        GROUP BY g.channel_id, g.d
    """)
    return con.execute("SELECT count(*) FROM labels").fetchone()[0]


def label_stats(con):
    """Баланс классов и контроль качества фильтра массовых дней."""
    return con.execute("""
        SELECT
            count(*)                                   AS канало_суток,
            sum(y)                                     AS позитивов,
            round(100.0 * sum(y) / count(*), 4)        AS доля_проц,
            round(100.0 * sum(CASE WHEN y = 1 AND dayofweek(d) IN (0, 6) THEN 1 ELSE 0 END)
                  / nullif(sum(y), 0), 1)              AS выходных_среди_позитивов_проц
        FROM labels
    """).df()
