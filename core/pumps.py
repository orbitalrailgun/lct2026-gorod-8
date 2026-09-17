"""M2 «Подтопление»: аномалия режима откачки.

Сценарий целиком продиктован заказчиком на встрече 17.09.2026. Его слова:

    «Насос будет триггерить: включился, выключился, включился, выключился, —
    и вот это его флаганье будет ярко светиться. Реальная аномалия.
    Не может насос работать как мигалка.»

И вторая часть, которой нет в ТЗ:

    «Протечка находится рядом с одной насосной, но из-за разуклона вода потекла
    дальше, и следующий коллектор становится затоплен. Насос, который находится
    не в первом коллекторе, а во втором, даёт сработку и начинает усиленно качать.
    Но мы никак не можем понять, откуда же вода.»

Отсюда две задачи: поймать аномальный режим откачки и указать, откуда пришла вода.

Что НЕ используется и почему:

* Значение «Затоплен» на каналах насосов — не гидрология, а бит слова неисправности:
  97,5 % таких событий приходят в пределах 5 секунд от предыдущего, цепочка
  «Неисправен → Затоплен → Обесточен», сезонный пик в августе вместо паводка.
  Оно идёт в отдельный признак деградации оборудования, но не в таргет воды.

* Датчики затопления (25 каналов) исключены: за 8 лет ни одного значения
  «Затоплен» или «Замкнут», а «Не замкнут» имеет медианную длительность 0 минут —
  это дребезг шлейфа, а не вода.
"""

import numpy as np

from core import config
from core.trits import DEVIATION, NORMAL, UNKNOWN

PUMP_TYPE = "Состояние насоса"
ON, OFF = "Включен", "Выключен"

# Порог аномалии по устойчивому z-score. Личные нормы насосов различаются
# от 1 до 45 пусков в сутки, поэтому глобального порога быть не может.
Z_THRESHOLD = 3.0

# Минимальный абсолютный эффект. Без него устойчивый z-score взрывается там,
# где разброс почти нулевой: насос, который стабильно делает 1 пуск в сутки,
# имеет MAD близкий к нулю, и два пуска дают формально огромное отклонение.
# Вердикт «2 пуска против нормы 1 — риск 99 %» уничтожил бы доверие диспетчера
# быстрее любой пропущенной аварии, поэтому аномалия требует и относительной,
# и абсолютной значимости одновременно.
MIN_ABS_STARTS = 10
MIN_ABS_EXCESS = 5

# Сутки считаются общесетевыми, если столько каналов разом превысили свою норму.
# За 8 лет таких суток 68, из них 38 в марте и 15 в апреле — подпись паводка.
NETWORK_HOT_MIN = 10


def build_pump_day(con):
    """Суточный счётчик пусков и наработки по каждому насосу.

    Пуск — переход «Выключен» → «Включен». Журнал насосов чистый: только 0,88 %
    событий повторяют предыдущее значение, поэтому переходы считаются надёжно
    и счётчик получается честным физическим показателем, а не артефактом опроса.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE pump_day AS
        WITH ev AS (
            SELECT j.channel_id, j.d, j.t, j.raw_value,
                   lag(j.raw_value) OVER (PARTITION BY j.channel_id ORDER BY j.d, j.t) AS prev,
                   lag(j.d)         OVER (PARTITION BY j.channel_id ORDER BY j.d, j.t) AS prev_d,
                   lag(j.t)         OVER (PARTITION BY j.channel_id ORDER BY j.d, j.t) AS prev_t
            FROM journal j
            JOIN dim_channel c ON c.channel_id = j.channel_id
            WHERE c.sensor_type = '{PUMP_TYPE}'
        )
        SELECT
            channel_id, d,
            count(*) FILTER (WHERE raw_value = '{ON}' AND prev = '{OFF}')  AS n_starts,
            count(*) FILTER (WHERE raw_value = '{OFF}' AND prev = '{ON}')  AS n_stops,
            -- наработка: сколько секунд насос простоял включённым за сутки
            COALESCE(sum(
                CASE WHEN raw_value = '{OFF}' AND prev = '{ON}' AND prev_d = d
                     THEN epoch(t) - epoch(prev_t) END
            ), 0) AS duty_seconds,
            count(*) AS n_ev
        FROM ev
        GROUP BY channel_id, d
    """)
    return con.execute("SELECT count(*) FROM pump_day").fetchone()[0]


def build_pump_baseline(con):
    """Личная сезонная норма насоса.

    Две нормировки сразу. Личная — потому что медианы каналов различаются
    от 1 до 45 пусков в сутки, и глобальный порог дал бы шквал ложных тревог
    на одних насосах и пропустил бы аварию на других. Сезонная — потому что
    март и апрель дают 38,3 % всех пусков за 8 лет, и без неё модель объявит
    аномалией весь паводок.

    Устойчивость: медиана и MAD вместо среднего и дисперсии, иначе выброс
    в 1777 пусков за сутки перекосит норму на год вперёд.
    """
    con.execute("""
        CREATE OR REPLACE TABLE pump_baseline AS
        WITH active AS (
            SELECT channel_id, month(d) AS mon, n_starts, duty_seconds
            FROM pump_day WHERE n_starts > 0
        ),
        med AS (
            SELECT channel_id, mon,
                   median(n_starts)              AS med_starts,
                   quantile_cont(n_starts, 0.95) AS p95_starts,
                   median(duty_seconds)          AS med_duty,
                   count(*)                      AS n_days
            FROM active GROUP BY channel_id, mon
        )
        SELECT m.*,
               -- MAD считается вторым проходом: агрегат поверх оконной функции
               -- в одном шаге недопустим, а устойчивая мера разброса здесь
               -- обязательна — выброс в 1777 пусков перекосил бы норму на год
               (SELECT median(abs(a.n_starts - m.med_starts))
                  FROM active a
                 WHERE a.channel_id = m.channel_id AND a.mon = m.mon) AS mad_starts
        FROM med m
    """)
    return con.execute("SELECT count(*) FROM pump_baseline").fetchone()[0]


def build_pump_anomaly(con, z_threshold=Z_THRESHOLD):
    """Отклонение режима откачки в тритах.

    Трит, а не флаг: насос без истории в этом месяце даёт UNKNOWN, а не «норму».
    Отсутствие нормы — это отсутствие знания, и вердикт обязан говорить именно так.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE pump_anomaly AS
        WITH j AS (
            SELECT p.channel_id, p.d, p.n_starts, p.duty_seconds,
                   b.med_starts, b.mad_starts, b.p95_starts, b.n_days
            FROM pump_day p
            LEFT JOIN pump_baseline b
                   ON b.channel_id = p.channel_id AND b.mon = month(p.d)
        )
        SELECT
            channel_id, d, n_starts, duty_seconds,
            med_starts, p95_starts,
            -- 1.4826 приводит MAD к масштабу стандартного отклонения
            CASE WHEN mad_starts > 0
                 THEN (n_starts - med_starts) / (1.4826 * mad_starts) END AS robust_z,
            CASE WHEN med_starts > 0 THEN n_starts / med_starts END       AS ratio_to_norm,
            CASE
                WHEN n_days IS NULL OR n_days < 5 THEN {UNKNOWN}   -- нормы ещё нет
                -- абсолютный фильтр: аномалия должна быть заметна не только
                -- относительно нормы, но и сама по себе
                WHEN n_starts < {MIN_ABS_STARTS}
                     OR n_starts - COALESCE(med_starts, 0) < {MIN_ABS_EXCESS}
                     THEN {NORMAL}
                WHEN mad_starts IS NULL OR mad_starts = 0 THEN
                     CASE WHEN n_starts > p95_starts THEN {DEVIATION} ELSE {NORMAL} END
                WHEN (n_starts - med_starts) / (1.4826 * mad_starts) >= {z_threshold}
                     THEN {DEVIATION}
                ELSE {NORMAL}
            END AS trit
        FROM j
    """)
    return con.execute("SELECT count(*) FROM pump_anomaly").fetchone()[0]


def build_network_context(con, hot_min=NETWORK_HOT_MIN):
    """Сколько насосов по всей сети аномальны в эти сутки.

    Это и есть ответ на вопрос «локальная авария или общий водоприток».
    Один горячий насос — течь, засор или отказ конкретного узла. Тридцать
    горячих в разных районах — паводок или ливень, и локальный ремонт не поможет.
    Различить это важно: диспетчер не должен гонять бригаду туда, где вода
    придёт снова через час.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE pump_network AS
        SELECT d,
               count(*) FILTER (WHERE trit = {DEVIATION})              AS n_hot,
               count(DISTINCT channel_id)                              AS n_active,
               count(*) FILTER (WHERE trit = {DEVIATION}) >= {hot_min} AS is_network_event
        FROM pump_anomaly
        GROUP BY d
    """)
    return con.execute("SELECT count(*) FROM pump_network").fetchone()[0]


def find_upstream_source(con, day, object_id, picket, radius=30):
    """Поиск источника воды вверх по трассе — кейс, который описал заказчик.

    Вода уходит по разуклону, поэтому сработавший насос часто стоит НИЖЕ места
    протечки. Ищем соседние пикеты того же объекта: если рядом есть насос,
    который молчит или не справляется, а выше по пикетам зафиксированы отклонения,
    это кандидат в источник.

    Уклон в данных не задан, поэтому «выше» понимается как меньший номер пикета —
    допущение, которое обязательно оговаривается в вердикте и в документации.
    """
    rows = con.execute(f"""
        SELECT c.picket, c.sensor_name, a.n_starts, a.med_starts, a.trit
        FROM pump_anomaly a
        JOIN dim_channel c ON c.channel_id = a.channel_id
        WHERE a.d = DATE '{day}'
          AND c.object_id = {object_id}
          AND c.picket BETWEEN {picket - radius} AND {picket + radius}
        ORDER BY c.picket
    """).df()
    return rows


def verdict_phrase(n_starts, med_starts, sensor_name, picket):
    """Формулировка, которую заказчик произнёс почти дословно.

    «АНС4 Н1 ПК231: 195 пусков за сутки против нормы 3» — фраза собирается
    из данных подстановкой, без интерпретации.
    """
    if med_starts and med_starts > 0:
        return (f"{sensor_name}: {n_starts:.0f} пусков за сутки "
                f"против нормы {med_starts:.0f}")
    return f"{sensor_name}: {n_starts:.0f} пусков за сутки, личной нормы ещё нет"
