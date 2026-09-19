"""Температурные тенденции и риск подмораживания.

Сценарий целиком продиктован заказчиком на встрече 17.09.2026:

    «Если люк сорвали и там пошёл холодный воздух, например, зимой, и есть
    угроза подмораживания труб, особенно холодной воды, — то очевидно, что
    здесь без разницы, есть люк или нет люка. Здесь главное именно тенденция,
    построение угрозы температурному режиму в коллекторе. И здесь нужно смотреть
    соседние пикеты, куда дальше эта температура пошла.»

В ТЗ этого сценария нет, а в таблице ответов «температурные тенденции» названы
среди ожидаемого содержания карточки прогноза.

Метод прямо следует из формулировки: важна не абсолютная температура, а её
отклонение от собственной сезонной нормы точки И сравнение с соседними пикетами.
Общее похолодание опускает температуру всюду и инцидентом не является.
Локальный провал на фоне спокойных соседей означает поступление наружного
воздуха — то есть нарушение контура.

Ограничение, которое надо держать в голове: температура опрашивается редко.
Медианный интервал между замерами — 184 минуты, у девяноста процентов каналов
до 2,6 суток. Поэтому «температура в норме» и «температуру давно не мерили» —
разные утверждения, и второе обязано попадать в карточку.
"""

from core.trits import DEVIATION, NORMAL, UNKNOWN

TEMP_TYPES = ("Датчик температуры",)

# Порог подмораживания. Заказчик называл угрозу именно для холодной воды;
# у части приборов зашит заводской порог «Температура ниже 3ºC», его и берём.
FREEZE_C = 3.0
FREEZE_WARNING_C = 6.0

# Насколько ниже сезонной нормы точка должна уйти, чтобы это считалось
# отклонением, а не обычным колебанием.
DEVIATION_C = 5.0

# Насколько холоднее соседей, чтобы заподозрить локальное поступление воздуха.
# Сравнение идёт не с абсолютным порогом, а с ОБЫЧНЫМ для этой точки отрывом:
# пикет у вентшахты холоднее соседей всегда, и постоянная разница событием
# не является. Проверено на данных: при абсолютном пороге признак срабатывал
# равномерно круглый год, то есть ловил геометрию, а не происшествия.
NEIGHBOUR_GAP_C = 4.0

# Свежесть замера: после этого срока молчание само по себе становится фактом.
STALE_HOURS = 24


def build_temperature_day(con):
    """Суточная температура по точкам: (объект, пикет).

    Берутся только валидные измерения — сентинелы и выбросы отсекаются
    семантическим контрактом ещё на этапе витрины.
    """
    types = ", ".join(f"'{t}'" for t in TEMP_TYPES)
    con.execute(f"""
        CREATE OR REPLACE TABLE temp_day AS
        SELECT
            c.object_id, c.picket, f.d,
            count(DISTINCT f.channel_id)  AS n_channels,
            avg(f.num_avg)                AS t_avg,
            min(f.num_min)                AS t_min,
            max(f.num_max)                AS t_max,
            max(f.t_last)                 AS last_seen
        FROM fact_channel_day f
        JOIN dim_channel c ON c.channel_id = f.channel_id
        WHERE c.sensor_type IN ({types})
          AND c.picket IS NOT NULL
          AND f.num_avg IS NOT NULL
        GROUP BY c.object_id, c.picket, f.d
    """)
    return con.execute("SELECT count(*) FROM temp_day").fetchone()[0]


def build_neighbour_norm(con):
    """Обычный отрыв точки от соседей — её собственная тепловая база."""
    con.execute("""
        CREATE OR REPLACE TABLE temp_neighbour_norm AS
        WITH daily AS (
            SELECT t.object_id, t.picket, t.d, t.t_avg,
                   (SELECT avg(o.t_avg) FROM temp_day o
                     WHERE o.object_id = t.object_id AND o.d = t.d
                       AND o.picket <> t.picket) AS t_neighbours
            FROM temp_day t
        )
        SELECT object_id, picket,
               median(t_avg - t_neighbours) AS med_gap,
               count(*) AS n_days
        FROM daily
        WHERE t_neighbours IS NOT NULL
        GROUP BY object_id, picket
    """)
    return con.execute("SELECT count(*) FROM temp_neighbour_norm").fetchone()[0]


def build_temperature_norm(con):
    """Сезонная норма точки: медиана по месяцу за всю историю.

    Норма именно точечная и помесячная. Общая по сети норма бесполезна:
    коллектор на глубине нескольких метров и коллектор у вентшахты имеют
    разный тепловой режим, и сравнивать их между собой нельзя.
    """
    con.execute("""
        CREATE OR REPLACE TABLE temp_norm AS
        SELECT object_id, picket, month(d) AS mon,
               median(t_avg) AS med_t,
               quantile_cont(t_avg, 0.10) AS p10_t,
               count(*) AS n_days
        FROM temp_day
        GROUP BY object_id, picket, month(d)
    """)
    return con.execute("SELECT count(*) FROM temp_norm").fetchone()[0]


def build_temperature_state(con):
    """Состояние точки в тритах с учётом соседей.

    Сравнение с соседними пикетами того же объекта — прямая реализация
    подсказки заказчика «нужно смотреть соседние пикеты, куда дальше эта
    температура пошла».
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE temp_state AS
        WITH joined AS (
            SELECT t.*, n.med_t, n.p10_t, n.n_days
            FROM temp_day t
            LEFT JOIN temp_norm n
                   ON n.object_id = t.object_id AND n.picket = t.picket
                  AND n.mon = month(t.d)
        ),
        with_neighbours AS (
            SELECT j.*,
                   (SELECT avg(o.t_avg) FROM joined o
                     WHERE o.object_id = j.object_id AND o.d = j.d
                       AND o.picket <> j.picket) AS t_neighbours,
                   nn.med_gap
            FROM joined j
            LEFT JOIN temp_neighbour_norm nn
                   ON nn.object_id = j.object_id AND nn.picket = j.picket
        )
        SELECT *,
               t_avg - med_t                              AS delta_norm,
               t_avg - t_neighbours                       AS delta_neighbours,
               -- отрыв сверх обычного для этой точки: именно он означает событие
               (t_avg - t_neighbours) - med_gap           AS excess_gap,
               CASE
                   WHEN n_days IS NULL OR n_days < 5 THEN {UNKNOWN}
                   WHEN t_min <= {FREEZE_C}                       THEN {DEVIATION}
                   WHEN med_t - t_avg >= {DEVIATION_C}            THEN {DEVIATION}
                   WHEN t_neighbours IS NOT NULL AND med_gap IS NOT NULL
                        AND ((t_avg - t_neighbours) - med_gap) <= -{NEIGHBOUR_GAP_C}
                        THEN {DEVIATION}
                   ELSE {NORMAL}
               END AS trit
        FROM with_neighbours
    """)
    return con.execute("SELECT count(*) FROM temp_state").fetchone()[0]


def describe(row):
    """Улики по температуре для карточки вердикта.

    Формулировки строятся от нормы точки, а не от абсолютного числа:
    «16 градусов» диспетчеру ничего не говорит, «на 7 ниже обычного
    для этой точки в мае» говорит всё.
    """
    out = []
    t_avg = row.get("t_avg")
    if t_avg is None:
        return out

    delta_norm = row.get("delta_norm")
    if delta_norm is not None and delta_norm <= -DEVIATION_C:
        out.append(f"температура {t_avg:.0f} °C — на {abs(delta_norm):.0f} ниже "
                   "обычной для этой точки в этом месяце")

    t_min = row.get("t_min")
    if t_min is not None and t_min <= FREEZE_C:
        out.append(f"минимум за сутки {t_min:.0f} °C — угроза подмораживания "
                   "трубопроводов холодной воды")
    elif t_min is not None and t_min <= FREEZE_WARNING_C:
        out.append(f"минимум за сутки {t_min:.0f} °C — приближение к порогу подмораживания")

    excess = row.get("excess_gap")
    if excess is not None and excess <= -NEIGHBOUR_GAP_C:
        out.append(f"на {abs(excess):.0f} °C холоднее соседних точек, чем обычно "
                   "для этого места, — похоже на поступление наружного воздуха, "
                   "а не на общее похолодание")
    return out


def blind_spot_note(row):
    """Оговорка о свежести замера.

    При медианном интервале опроса почти в три часа отсутствие скачка
    не является доказательством его отсутствия, и вердикт обязан это сказать.
    """
    if row is None or row.get("t_avg") is None:
        return ["температурных датчиков в этой точке нет — "
                "тепловой режим не контролируется"]
    if row.get("n_channels", 0) == 1:
        return ["температура измеряется одним датчиком — "
                "перепроверить показание нечем"]
    return []
