"""Признаки модели.

Главный принцип: признак проектируется как высказывание. У каждого есть шаблон
фразы и своя норма, поэтому объяснение вердикта собирается подстановкой чисел,
а не генерируется языковой моделью. Текст детерминирован и не может разойтись
с данными — это и есть защита от красивого, но неверного обоснования.

Все окна заканчиваются днём D включительно, метка относится к D+1. Ничего из
будущего в признаки не попадает.
"""

from core import config

# Реестр признаков: имя -> как объяснить его диспетчеру.
#   phrase   — шаблон фразы, {v} значение признака, {base} личная норма канала
#   unit     — единица, попадает в текст
#   group    — раздел карточки улик
#   higher_is_worse — направление: нужно, чтобы вклад читался как «хуже/лучше»
FEATURES = {
    "ev_7d": {
        "phrase": "событий за 7 суток: {v:.0f} при обычных {base:.0f}",
        "group": "активность", "higher_is_worse": True,
    },
    "ev_ratio_7d": {
        "phrase": "темп событий к личной норме канала: ×{v:.1f}",
        "group": "активность", "higher_is_worse": True,
    },
    "active_days_7d": {
        "phrase": "канал выходил на связь {v:.0f} суток из 7",
        "group": "активность", "higher_is_worse": False,
    },
    "silence_days": {
        "phrase": "молчит {v:.0f} суток подряд",
        "group": "молчание", "higher_is_worse": True,
    },
    "silence_ratio": {
        "phrase": "молчание превышает обычный для канала интервал в {v:.1f} раза",
        "group": "молчание", "higher_is_worse": True,
    },
    "fault_ev_7d": {
        "phrase": "сообщений о неисправности за 7 суток: {v:.0f}",
        "group": "отказы", "higher_is_worse": True,
    },
    "fault_days_30d": {
        "phrase": "суток с неисправностью за месяц: {v:.0f}",
        "group": "отказы", "higher_is_worse": True,
    },
    "fault_ratio_90d": {
        "phrase": "неисправностей в {v:.1f} раза больше личной нормы канала",
        "group": "отказы", "higher_is_worse": True,
    },
    "days_since_fault": {
        "phrase": "последняя неисправность: {v:.0f} суток назад",
        "group": "отказы", "higher_is_worse": False,
    },
    "undefined_ev_7d": {
        "phrase": "потерь связи за 7 суток: {v:.0f}",
        "group": "связь", "higher_is_worse": True,
    },
    "undefined_share_7d": {
        "phrase": "доля событий без определённого состояния: {v:.0%}",
        "group": "связь", "higher_is_worse": True,
    },
    "dev_days_7d": {
        "phrase": "суток с отклонением за неделю: {v:.0f} из 7",
        "group": "состояние", "higher_is_worse": True,
    },
    "unknown_days_7d": {
        "phrase": "суток без наблюдаемости за неделю: {v:.0f} из 7",
        "group": "состояние", "higher_is_worse": True,
    },
    "domain_size": {
        "phrase": "в этой точке {v:.0f} каналов",
        "group": "окружение", "higher_is_worse": False,
    },
    "domain_dev_share_7d": {
        "phrase": "соседей по точке в отклонении: {v:.0%}",
        "group": "окружение", "higher_is_worse": True,
    },
    "domain_silent_share": {
        "phrase": "соседей по точке молчат: {v:.0%}",
        "group": "окружение", "higher_is_worse": True,
    },
    "base_failed_share_7d": {
        "phrase": "каналов контроллера в отказе: {v:.0%}",
        "group": "инфраструктура", "higher_is_worse": True,
    },
    "channel_age_days": {
        "phrase": "канал в эксплуатации {v:.0f} суток",
        "group": "контекст", "higher_is_worse": False,
    },
    "month": {
        "phrase": "месяц {v:.0f}",
        "group": "контекст", "higher_is_worse": False,
    },
    "is_weekend": {
        "phrase": "выходной день",
        "group": "контекст", "higher_is_worse": False,
    },
}

FEATURE_NAMES = list(FEATURES)

# Минимальная абсолютная опора для относительных признаков.
# Кратность, посчитанная от одного события, формально верна и практически
# бессмысленна: «неисправностей в 12,9 раза больше нормы» рядом с «сообщений
# за 7 суток: 1» выглядит как ошибка системы и подрывает доверие к остальным
# уликам. Такая улика в карточку не попадает, хотя в модели признак остаётся.
MIN_SUPPORT = {
    "fault_ratio_90d": ("fault_ev_7d", 3),
    "ev_ratio_7d": ("ev_7d", 5),
    "silence_ratio": ("silence_days", 2),
    "undefined_share_7d": ("undefined_ev_7d", 3),
}


def is_supported(name, values):
    """Есть ли у относительной улики достаточная абсолютная опора."""
    rule = MIN_SUPPORT.get(name)
    if rule is None:
        return True
    source, floor = rule
    try:
        return float(values.get(source, 0)) >= floor
    except (TypeError, ValueError):
        return True


SPECIAL = {
    # «0 суток назад» звучит как ошибка, хотя означает самое тревожное — сегодня
    "days_since_fault": lambda v: ("неисправность зафиксирована сегодня" if v < 1
                                   else f"последняя неисправность: {v:.0f} суток назад"),
    "is_weekend": lambda v: "выходной день" if v else "будний день",
    "silence_days": lambda v: ("канал выходил на связь сегодня" if v < 1
                               else f"молчит {v:.0f} суток подряд"),
}


def describe(name, value, baseline=None):
    """Человеческая фраза про конкретное значение признака."""
    if name in SPECIAL:
        return SPECIAL[name](value)
    spec = FEATURES.get(name)
    if spec is None:
        return f"{name} = {value}"
    try:
        return spec["phrase"].format(v=value, base=baseline if baseline is not None else 0)
    except (KeyError, ValueError):
        return f"{name} = {value}"


def build_features(con, train_from=None, train_to=None):
    """Строит матрицу признаков на сетке меток.

    Плотная серия канало-суток нужна потому, что журнал событийный: молчание
    не порождает строк, а оно и есть самый сильный признак отказа. Нули
    проставляются явно, чтобы окна считались по календарю, а не по событиям.
    """
    where = ""
    if train_from and train_to:
        where = f"WHERE l.d BETWEEN DATE '{train_from}' AND DATE '{train_to}'"

    con.execute(f"""
        CREATE OR REPLACE TABLE features AS
        WITH dense AS (
            -- сетка меток + факты: отсутствующие сутки становятся нулями
            SELECT
                l.channel_id, l.d, l.y,
                COALESCE(f.n_ev, 0)                       AS n_ev,
                COALESCE(f.n_fault + f.n_disabled, 0)     AS n_fault,
                COALESCE(f.n_undefined, 0)                AS n_undefined,
                CASE WHEN f.channel_id IS NULL THEN 0 ELSE 1 END AS was_active,
                COALESCE(f.trit_day, 0)                   AS trit_day
            FROM labels l
            LEFT JOIN fact_channel_day f
                   ON f.channel_id = l.channel_id AND f.d = l.d
            {where}
        ),
        win AS (
            SELECT
                d.*,
                -- окна заканчиваются текущими сутками включительно
                SUM(n_ev)       OVER w7  AS ev_7d,
                SUM(was_active) OVER w7  AS active_days_7d,
                SUM(n_fault)    OVER w7  AS fault_ev_7d,
                SUM(n_undefined)OVER w7  AS undefined_ev_7d,
                SUM(CASE WHEN trit_day = 1 THEN 1 ELSE 0 END) OVER w7  AS dev_days_7d,
                SUM(CASE WHEN was_active = 0 THEN 1 ELSE 0 END) OVER w7 AS unknown_days_7d,
                SUM(CASE WHEN n_fault > 0 THEN 1 ELSE 0 END) OVER w30 AS fault_days_30d,
                SUM(n_ev)       OVER w90 AS ev_90d,
                SUM(n_fault)    OVER w90 AS fault_90d,
                SUM(was_active) OVER w90 AS active_days_90d
            FROM dense d
            WINDOW
                w7  AS (PARTITION BY channel_id ORDER BY d ROWS BETWEEN 6  PRECEDING AND CURRENT ROW),
                w30 AS (PARTITION BY channel_id ORDER BY d ROWS BETWEEN 29 PRECEDING AND CURRENT ROW),
                w90 AS (PARTITION BY channel_id ORDER BY d ROWS BETWEEN 89 PRECEDING AND CURRENT ROW)
        ),
        streak AS (
            -- сколько суток подряд канал молчит на момент D
            SELECT w.*,
                   d - COALESCE(MAX(CASE WHEN was_active = 1 THEN d END)
                                OVER (PARTITION BY channel_id ORDER BY d
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), d) AS silence_days,
                   d - COALESCE(MAX(CASE WHEN n_fault > 0 THEN d END)
                                OVER (PARTITION BY channel_id ORDER BY d
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), d - 9999) AS days_since_fault
            FROM win w
        ),
        dom AS (
            -- состояние соседей по точке: тот самый логический домен
            SELECT s.channel_id, s.d,
                   COALESCE(g.n_channels, 1)                        AS domain_size,
                   COALESCE(n.dev_share, 0.0)                        AS domain_dev_share_7d,
                   COALESCE(n.silent_share, 0.0)                     AS domain_silent_share
            FROM streak s
            LEFT JOIN dim_channel c   ON c.channel_id = s.channel_id
            LEFT JOIN logical_domain g ON g.object_id = c.object_id AND g.picket = c.picket
            LEFT JOIN (
                SELECT c2.object_id, c2.picket, f2.d,
                       AVG(CASE WHEN f2.trit_day = 1 THEN 1.0 ELSE 0.0 END) AS dev_share,
                       AVG(CASE WHEN f2.trit_day = 0 THEN 1.0 ELSE 0.0 END) AS silent_share
                FROM fact_channel_day f2
                JOIN dim_channel c2 ON c2.channel_id = f2.channel_id
                WHERE c2.picket IS NOT NULL
                GROUP BY c2.object_id, c2.picket, f2.d
            ) n ON n.object_id = c.object_id AND n.picket = c.picket AND n.d = s.d
        ),
        infra AS (
            -- каскад по контроллеру: обрыв питающей линии 48 В роняет десятки каналов
            SELECT c.tag_base, f.d,
                   AVG(CASE WHEN f.n_fault + f.n_disabled > 0 THEN 1.0 ELSE 0.0 END) AS base_failed_share
            FROM fact_channel_day f
            JOIN dim_channel c ON c.channel_id = f.channel_id
            GROUP BY c.tag_base, f.d
        )
        SELECT
            s.channel_id, s.d, s.y,
            s.ev_7d::DOUBLE                                                  AS ev_7d,
            (s.ev_7d * 1.0 / nullif(s.ev_90d / 90.0 * 7.0, 0))               AS ev_ratio_7d,
            s.active_days_7d::DOUBLE                                         AS active_days_7d,
            s.silence_days::DOUBLE                                           AS silence_days,
            (s.silence_days * 1.0 / nullif(90.0 / nullif(s.active_days_90d, 0), 0)) AS silence_ratio,
            s.fault_ev_7d::DOUBLE                                            AS fault_ev_7d,
            s.fault_days_30d::DOUBLE                                         AS fault_days_30d,
            (s.fault_ev_7d * 1.0 / nullif(s.fault_90d / 90.0 * 7.0, 0))      AS fault_ratio_90d,
            LEAST(s.days_since_fault, 9999)::DOUBLE                          AS days_since_fault,
            s.undefined_ev_7d::DOUBLE                                        AS undefined_ev_7d,
            (s.undefined_ev_7d * 1.0 / nullif(s.ev_7d, 0))                   AS undefined_share_7d,
            s.dev_days_7d::DOUBLE                                            AS dev_days_7d,
            s.unknown_days_7d::DOUBLE                                        AS unknown_days_7d,
            dom.domain_size::DOUBLE                                          AS domain_size,
            dom.domain_dev_share_7d                                          AS domain_dev_share_7d,
            dom.domain_silent_share                                          AS domain_silent_share,
            COALESCE(i.base_failed_share, 0.0)                               AS base_failed_share_7d,
            date_diff('day', p.first_day, s.d)::DOUBLE                       AS channel_age_days,
            month(s.d)::DOUBLE                                               AS month,
            CASE WHEN dayofweek(s.d) IN (0, 6) THEN 1.0 ELSE 0.0 END         AS is_weekend
        FROM streak s
        JOIN dom ON dom.channel_id = s.channel_id AND dom.d = s.d
        JOIN dim_channel c ON c.channel_id = s.channel_id
        JOIN channel_profile p ON p.channel_id = s.channel_id
        LEFT JOIN infra i ON i.tag_base = c.tag_base AND i.d = s.d
    """)
    return con.execute("SELECT count(*) FROM features").fetchone()[0]
