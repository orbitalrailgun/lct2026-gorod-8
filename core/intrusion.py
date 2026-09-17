"""M4 «Несанкционированный доступ»: правиловый детектор.

Метод продиктован заказчиком на встрече 17.09.2026:

    «Если я вижу последовательность срабатывания тревожных сигналов, что он ходит
    туда-обратно, и дверь открыл, и потом другой объёмник сработал — здесь можно
    это трактовать как действительно проникновение.»

И там же — критерий отсечения одиночных ложных:

    «Если нету вентшахт, люков, и просто в середине коллектора сработал какой-то
    объёмный датчик — очевидно, что скорее всего это ложная тревога.»

Приоритет сценария заказчик понизил сам: уникальные истории вроде вандализма
«рассматриваем второстепенным образом, потому что много внимания уберёт, а пользы
не получим». Поэтому здесь правила, а не отдельная модель — и это заявляется прямо.

Троичная логика работает на режиме охраны. Сработка датчика считается значимой
только тогда, когда объект заведомо был под охраной:

    confirm(сработка, режим_охраны)

    +1  сработка при подтверждённом режиме охраны   — кандидат в проникновение
    -1  объект снят с охраны                         — это работа подрядчиков
     0  канала охраны нет или он залип               — «не знаю», а не «всё чисто»

Третий случай не редкость: каналов «Состояние охраны» всего 47 на 55+ объектов,
а ещё часть из них залипла.
"""

from core.trits import DEVIATION, NORMAL, UNKNOWN

GUARD_STATE = "Состояние охраны"
ARMED, DISARMED = "На охране", "Снято с охраны"

# Типы, сработка которых может означать присутствие человека.
INTRUSION_TYPES = ("Датчик движения", "КД Дверь", "КД Люк", "Стекло",
                   "КД АВ", "9-секционный люк")

# Значения, означающие сработку у контактных и объёмных датчиков.
TRIGGER_VALUES = ("Обнаружено движение", "Не замкнут", "Разбито")

# Канал охраны считается неисправным, если объект «под охраной» почти всегда
# либо почти не переключается. В данных 9 баз показывают 98,2-99,9 % времени
# под охраной при полусотне переключений за 8 лет — это залипание, а не режим.
STUCK_ARMED_SHARE = 0.98
STUCK_MIN_SWITCHES = 200

# Ночное окно: присутствие человека в это время объяснить труднее.
NIGHT_FROM, NIGHT_TO = 0, 5

# Верхняя граница правдоподобия для человека. Проникновение даёт десятки
# срабатываний, а не тысячи: в данных есть эпизод с 3 303 сработками за ночь
# в одной точке — это дребезг шлейфа, а не перемещение. Такой эпизод должен
# уходить в отказ оборудования, а не в охранную тревогу.
HUMAN_PLAUSIBLE_MAX = 200

# Верхняя граница длительности эпизода. Проникновение — это минуты, максимум
# час-другой. Эпизод на двенадцать часов означает либо работу подрядчиков при
# формально не снятой охране, либо залипший датчик, но не человека внутри.
# Признак стал виден только после перехода на секундные метки: в посуточном
# представлении «84 срабатывания» и «84 срабатывания за 12 часов» неразличимы.
HUMAN_PLAUSIBLE_HOURS = 4


def build_armed_intervals(con):
    """Интервалы «объект под охраной», восстановленные из переключений."""
    con.execute(f"""
        CREATE OR REPLACE TABLE armed_interval AS
        WITH ev AS (
            SELECT c.object_id, j.channel_id, j.d, j.t, j.raw_value,
                   (j.d + j.t) AS ts,
                   lead(j.d + j.t) OVER (PARTITION BY j.channel_id ORDER BY j.d, j.t) AS next_ts
            FROM journal j
            JOIN dim_channel c ON c.channel_id = j.channel_id
            WHERE c.sensor_type = '{GUARD_STATE}'
              AND j.raw_value IN ('{ARMED}', '{DISARMED}')
        )
        SELECT object_id, channel_id, ts AS t_from, next_ts AS t_to,
               (raw_value = '{ARMED}') AS is_armed
        FROM ev WHERE next_ts IS NOT NULL
    """)
    return con.execute("SELECT count(*) FROM armed_interval").fetchone()[0]


def build_guard_health(con):
    """Исправность канала охраны.

    Без этой проверки детектор на залипших каналах объявит проникновением любое
    движение: объект там «под охраной» круглосуточно годами. Вердикт по таким
    объектам обязан опираться на время суток, а не на режим, и честно это писать.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE guard_health AS
        WITH agg AS (
            SELECT object_id, channel_id,
                   count(*) AS n_switches,
                   sum(CASE WHEN is_armed THEN epoch(t_to) - epoch(t_from) ELSE 0 END) AS armed_sec,
                   sum(epoch(t_to) - epoch(t_from)) AS total_sec
            FROM armed_interval GROUP BY object_id, channel_id
        )
        SELECT *,
               armed_sec / nullif(total_sec, 0) AS armed_share,
               (armed_sec / nullif(total_sec, 0) >= {STUCK_ARMED_SHARE}
                OR n_switches < {STUCK_MIN_SWITCHES}) AS is_stuck
        FROM agg
    """)
    return con.execute("SELECT count(*) FROM guard_health").fetchone()[0]


def build_intrusion_events(con, window_minutes=15):
    """Сработки охранного контура с тритом режима охраны и кратностью.

    Кратность считается по РАЗНЫМ типам датчиков в окне: именно последовательность
    «дверь открылась, затем сработал объёмник» заказчик назвал признаком реального
    проникновения. Повтор одного и того же датчика такой силы не имеет.
    """
    types = ", ".join(f"'{t}'" for t in INTRUSION_TYPES)
    values = ", ".join(f"'{v}'" for v in TRIGGER_VALUES)
    con.execute(f"""
        CREATE OR REPLACE TABLE intrusion_event AS
        WITH trig AS (
            SELECT j.channel_id, j.d, j.t, (j.d + j.t) AS ts, j.raw_value,
                   c.object_id, c.object_name, c.parent_name, c.picket,
                   c.sensor_type, c.sensor_name
            FROM journal j
            JOIN dim_channel c ON c.channel_id = j.channel_id
            WHERE c.sensor_type IN ({types})
              AND j.raw_value IN ({values})
              AND c.picket IS NOT NULL
        ),
        with_mode AS (
            SELECT t.*,
                   a.is_armed,
                   h.is_stuck
            FROM trig t
            LEFT JOIN armed_interval a
                   ON a.object_id = t.object_id
                  AND t.ts >= a.t_from AND t.ts < a.t_to
            LEFT JOIN guard_health h
                   ON h.channel_id = a.channel_id
        ),
        grouped AS (
            SELECT object_id, object_name, parent_name, picket, d,
                   min(t) AS first_t, max(t) AS last_t,
                   count(*) AS n_triggers,
                   count(DISTINCT sensor_type) AS n_types,
                   count(DISTINCT channel_id) AS n_channels,
                   list(DISTINCT sensor_type) AS types,
                   any_value(sensor_name) AS sample_name,
                   -- режим охраны в трите: неизвестность не превращается в «снято»
                   max(CASE
                         WHEN is_armed IS NULL OR is_stuck THEN {UNKNOWN}
                         WHEN is_armed THEN {DEVIATION}
                         ELSE {NORMAL}
                       END) AS armed_trit,
                   bool_or(COALESCE(is_stuck, FALSE)) AS guard_unreliable,
                   min(hour(t)) AS min_hour
            FROM with_mode
            GROUP BY object_id, object_name, parent_name, picket, d
        )
        SELECT *,
               (min_hour BETWEEN {NIGHT_FROM} AND {NIGHT_TO}) AS is_night
        FROM grouped
    """)
    return con.execute("SELECT count(*) FROM intrusion_event").fetchone()[0]


def episode_hours(row):
    """Длительность эпизода в часах по секундным меткам."""
    first, last = row.get("first_t"), row.get("last_t")
    if first is None or last is None:
        return None
    try:
        return (_seconds(last) - _seconds(first)) / 3600.0
    except (TypeError, ValueError):
        return None


def _seconds(value):
    """Секунды от полуночи для time или timedelta из pandas."""
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    return value.hour * 3600 + value.minute * 60 + value.second


def score_intrusion(row):
    """Скор и обоснование. Правила, а не модель — размечать проникновения нечем."""
    score, why = 0.0, []

    hours = episode_hours(row)
    if hours is not None and hours > HUMAN_PLAUSIBLE_HOURS:
        return 0.0, [f"эпизод длился {hours:.1f} ч — для присутствия человека "
                     "это неправдоподобно долго. Картина соответствует работе "
                     "подрядчиков при не снятой охране либо залипшему датчику, "
                     "а не проникновению"]

    n_types = int(row["n_types"])
    if n_types >= 3:
        score += 0.45
        why.append(f"сработали датчики {n_types} разных типов — "
                   "последовательность похожа на перемещение человека")
    elif n_types == 2:
        score += 0.30
        why.append("сработали датчики двух разных типов подряд")
    else:
        score += 0.08
        why.append(f"сработал один тип датчика ({row['types'][0]})")

    trit = int(row["armed_trit"])
    if trit == DEVIATION:
        score += 0.35
        why.append("объект в этот момент был под охраной")
    elif trit == NORMAL:
        score -= 0.25
        why.append("объект был снят с охраны — вероятно, работа подрядчиков")

    if row["is_night"]:
        score += 0.20
        why.append(f"время срабатывания — {int(row['min_hour']):02d} ч, "
                   "присутствие людей в это время не регламентно")

    n_trig = int(row["n_triggers"])
    if n_trig > HUMAN_PLAUSIBLE_MAX:
        # Физически неправдоподобно для человека — почти наверняка дребезг
        return 0.0, [f"срабатываний за эпизод: {n_trig}. Для перемещения человека "
                     "это неправдоподобно много — картина соответствует дребезгу "
                     "шлейфа, а не проникновению; эпизод отнесён к отказам оборудования"]
    if n_trig >= 5:
        score += 0.10
        why.append(f"срабатываний за эпизод: {n_trig}")

    return max(0.0, min(1.0, score)), why


def blind_spot_note(row):
    """Оговорки о наблюдаемости для охранного вердикта."""
    notes = []
    if int(row["armed_trit"]) == UNKNOWN:
        if row["guard_unreliable"]:
            notes.append("канал состояния охраны на объекте недостоверен "
                         "(держит режим почти постоянно) — решение принято "
                         "по времени суток и кратности, а не по режиму")
        else:
            notes.append("канала состояния охраны на объекте нет — "
                         "неизвестно, был ли он под охраной")
    return notes
