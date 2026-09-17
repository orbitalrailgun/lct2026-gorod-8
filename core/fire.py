"""M3 «Пожар»: многофакторный детектор.

Supervised-классификатор здесь построить не на чем, и это установлено, а не
предположено: за 8 лет после отсева регламентных прогонов остаётся порядка
11 кандидатов «дата × пикет», причём 96,1 % тревог пожарной подсистемы —
это неисправности оборудования, а не возгорания. Сработка дымового датчика
сама по себе ничего не значит: за историю сработали ВСЕ 4 337 дымовых каналов,
медиана 28 раз на канал.

Поэтому детектор строится на пространственно-временном паттерне и на подтверждении,
а качество предъявляется как precision@k на экспертной разметке, а не как accuracy.

Троичная логика здесь работает в полную силу. Подтверждение задымления теплом
доступно лишь в 135 логических доменах из 2 140 с дымовыми датчиками. В остальных
`confirm(дым, термика)` возвращает UNKNOWN — и вердикт честно пишет «подтвердить
нечем», вместо того чтобы округлить отсутствие подтверждения до его отсутствия.
"""

from core.trits import DEVIATION, NORMAL, UNKNOWN

SMOKE_TYPE = "Датчик дыма"
HEAT_TYPES = ("Тепловой датчик", "Датчик температуры")
MANUAL_TYPE = "Ручной извещатель"

SMOKE_ON = "Обнаружен дым"
HEAT_TRIP = "Не замкнут"

# Прогон ТО: доля сработавших дымовых каналов базы за сутки. Ниже этого порога
# событие считается локальным. 645 базо-дней в истории превышают 0.8.
SWEEP_SHARE = 0.5
SWEEP_MIN_PICKETS = 3


def build_smoke_events(con):
    """Сработки дыма по пикетам с признаком массовости.

    Массовость считается сразу: без неё детектор 645 раз объявит пожар во всём
    коллекторе, а это регламентные проверки. У одной базы зафиксировано 27 дней,
    когда срабатывали все 96 дымовых каналов по всем 45 пикетам разом.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE smoke_event AS
        WITH ev AS (
            SELECT j.channel_id, j.d, j.t, c.object_id, c.picket,
                   c.tag_base, c.sensor_name
            FROM journal j
            JOIN dim_channel c ON c.channel_id = j.channel_id
            WHERE c.sensor_type = '{SMOKE_TYPE}' AND j.raw_value = '{SMOKE_ON}'
        ),
        base_size AS (
            SELECT tag_base, count(*) AS n_smoke_total
            FROM dim_channel WHERE sensor_type = '{SMOKE_TYPE}' GROUP BY tag_base
        ),
        per_base_day AS (
            SELECT e.tag_base, e.d,
                   count(DISTINCT e.channel_id) AS n_fired,
                   count(DISTINCT e.picket)     AS n_pickets
            FROM ev e GROUP BY e.tag_base, e.d
        )
        SELECT e.*, p.n_fired, p.n_pickets, b.n_smoke_total,
               p.n_fired * 1.0 / nullif(b.n_smoke_total, 0) AS base_share,
               (p.n_fired * 1.0 / nullif(b.n_smoke_total, 0) >= {SWEEP_SHARE}
                AND p.n_pickets >= {SWEEP_MIN_PICKETS})     AS is_sweep
        FROM ev e
        JOIN per_base_day p ON p.tag_base = e.tag_base AND p.d = e.d
        JOIN base_size b    ON b.tag_base = e.tag_base
    """)
    return con.execute("SELECT count(*) FROM smoke_event").fetchone()[0]


def build_heat_events(con):
    """Термические сработки: подтверждающий сигнал.

    У теплового датчика и ручного извещателя нет состояния «пожар» — их сработка
    кодируется размыканием шлейфа. Без явного знания об этом сервис молча
    пропускал бы 100 % их срабатываний и показывал диспетчеру бессмысленное
    «Не замкнут».
    """
    heat_list = ", ".join(f"'{t}'" for t in HEAT_TYPES)
    con.execute(f"""
        CREATE OR REPLACE TABLE heat_event AS
        SELECT j.channel_id, j.d, j.t, c.object_id, c.picket, c.sensor_type,
               c.sensor_name, j.raw_value, j.num_value
        FROM journal j
        JOIN dim_channel c ON c.channel_id = j.channel_id
        WHERE c.sensor_type IN ({heat_list})
          AND (j.raw_value = '{HEAT_TRIP}'
               OR (j.num_value IS NOT NULL AND j.num_value >= 40))
    """)
    return con.execute("SELECT count(*) FROM heat_event").fetchone()[0]


def build_fire_candidates(con, window_minutes=30):
    """Кандидаты в пожар: локальная кратность плюс подтверждение.

    Трит подтверждения собирается по правилу Клини:
      +1  термика в этой точке сработала в окне       — подтверждено
      -1  термика есть и молчит                        — не подтверждено
       0  термики в точке нет                          — подтвердить нечем

    Третий случай — не отговорка, а факт: он покрывает 2 005 доменов из 2 140.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE fire_candidate AS
        WITH smoke_day AS (
            SELECT object_id, picket, d,
                   count(DISTINCT channel_id) AS n_smoke_ch,
                   count(*)                   AS n_smoke_ev,
                   min(t)                     AS first_t,
                   max(t)                     AS last_t,
                   bool_or(is_sweep)          AS is_sweep,
                   max(base_share)            AS base_share,
                   any_value(sensor_name)     AS sample_name
            FROM smoke_event
            WHERE picket IS NOT NULL
            GROUP BY object_id, picket, d
        ),
        heat_day AS (
            SELECT object_id, picket, d, count(*) AS n_heat_ev, min(t) AS heat_t
            FROM heat_event WHERE picket IS NOT NULL
            GROUP BY object_id, picket, d
        )
        SELECT
            s.object_id, s.picket, s.d,
            s.n_smoke_ch, s.n_smoke_ev, s.first_t, s.last_t,
            s.is_sweep, s.base_share, s.sample_name,
            COALESCE(h.n_heat_ev, 0) AS n_heat_ev,
            g.has_thermal,
            g.n_channels AS domain_size,
            CASE
                WHEN NOT COALESCE(g.has_thermal, FALSE) THEN {UNKNOWN}
                WHEN h.n_heat_ev > 0
                     AND abs(epoch(h.heat_t) - epoch(s.first_t)) <= {window_minutes * 60}
                     THEN {DEVIATION}
                ELSE {NORMAL}
            END AS confirm_trit
        FROM smoke_day s
        LEFT JOIN heat_day h
               ON h.object_id = s.object_id AND h.picket = s.picket AND h.d = s.d
        LEFT JOIN logical_domain g
               ON g.object_id = s.object_id AND g.picket = s.picket
    """)
    return con.execute("SELECT count(*) FROM fire_candidate").fetchone()[0]


def score_candidate(row):
    """Скор доверия к кандидату и его словесное обоснование.

    Веса подобраны по смыслу, а не обучены: обучать не на чем. Это заявляется
    прямо, а сам скор калибруется на 11 проверочных кейсах, выделенных аудитом.
    """
    score, why = 0.0, []

    if row["is_sweep"]:
        # Главный подавитель: сработало полбазы за сутки по трём и более пикетам
        return 0.0, [f"сработало {row['base_share']:.0%} дымовых датчиков контроллера "
                     f"— это регламентная проверка, пожар так не выглядит"]

    n_ch = int(row["n_smoke_ch"])
    if n_ch >= 3:
        score += 0.45
        why.append(f"в одной точке сработало {n_ch} независимых дымовых датчика")
    elif n_ch == 2:
        score += 0.30
        why.append("в одной точке сработали два независимых дымовых датчика")
    else:
        score += 0.10
        why.append("сработал один дымовой датчик")

    trit = int(row["confirm_trit"])
    if trit == DEVIATION:
        score += 0.40
        why.append("подтверждено термическим датчиком в том же пикете")
    elif trit == NORMAL:
        score -= 0.15
        why.append("термический датчик в этой точке сработку не подтвердил")

    if int(row["n_smoke_ev"]) >= 10:
        score += 0.10
        why.append(f"сработок за сутки: {int(row['n_smoke_ev'])} — устойчивое задымление")

    return max(0.0, min(1.0, score)), why


def blind_spot_note(row):
    """Строка «чего мы не видим» для пожарного вердикта."""
    if int(row["confirm_trit"]) == UNKNOWN:
        return ["тепловых датчиков в этой точке нет — подтвердить задымление нечем; "
                "это не значит, что температура в норме"]
    return []
