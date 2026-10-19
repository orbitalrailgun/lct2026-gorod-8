"""Синтетический экспериментальный объект.

Организаторы разрешили синтезировать данные, выделив их в отдельный
экспериментальный объект, и спросили: какой набор датчиков и с какими
характеристиками нужно поставить, чтобы точность достигла требуемой.

Ответить на это честно можно ровно одним способом — разделив то, что
происходит в коллекторе, и то, что мы об этом узнаём.

    ФИЗИКА        порождается один раз и не зависит от оснащения.
                  Приборы стареют и ломаются, в приямок прибывает вода,
                  тлеет кабель, кто-то вскрывает люк. Это «истина»:
                  мы знаем, что и когда произошло.

    НАБЛЮДАЕМОСТЬ зависит от оснащения целиком. Какие датчики стоят,
                  как часто опрашиваются, через сколько пикетов, с каким
                  порогом — всё это определяет, какая часть физики попадёт
                  в журнал и с какой задержкой.

Эксперимент прогоняет ОДНУ И ТУ ЖЕ физику через разные конфигурации
наблюдаемости и меряет, что изменилось в прогнозе. Модель при этом
не переобучается: берётся та же обученная модель, что работает
на реальных данных. Поэтому разница в метриках — следствие оснащения,
а не наших настроек генератора.

Два свойства, ради которых всё и затевалось.

Первое: синтетический объект — единственное место, где у инцидентов
есть разметка. На реальных данных её нет, и Precision по пожару посчитать
не с чем. Здесь мы знаем, что пожар был, и можем честно сказать, заметил
его сервис или нет.

Второе: базовый набор приборов одинаков во всех конфигурациях, и метрики
отказов считаются только по нему. Иначе сравнение было бы нечестным:
добавив датчиков, мы добавили бы и новых кандидатов на поломку.

Все распределения взяты из `models/sensor_calibration.json` — это
измерения на поставленных данных, а не придуманные числа.
"""

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from core import config, contract

CALIBRATION = os.path.join(config.MODEL_DIR, "sensor_calibration.json")

# Экспериментальный объект живёт в своём диапазоне идентификаторов,
# чтобы ни одна выборка по реальным данным не могла его зацепить.
EXPERIMENT_OBJECT_ID = 99_001
EXPERIMENT_PARENT_ID = 99_000
EXPERIMENT_OBJECT_NAME = "объект Эксперимент"
CHANNEL_ID_BASE = 900_000

# Базовый набор: то, что стоит в коллекторе всегда. Метрики прогноза
# отказов считаются только по этим приборам, поэтому во всех конфигурациях
# он одинаков.
CORE_SENSORS = ("Датчик дыма",)

# Словарь значений по типам — ровно тот, что знает семантический контракт.
# Значение не из словаря контракт отнесёт к мусору, и запись пропадёт зря.
VALUES = {
    "Датчик дыма":        {"норма": "Дыма нет", "сработка": "Обнаружен дым",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "Тепловой датчик":    {"норма": "Норма", "сработка": "Не замкнут",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "Датчик температуры": {"норма": "В норме от +3 до +40",
                           "сработка": "Температура ниже 3ºC",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "Датчик затопления":  {"норма": "Норма", "сработка": "Не замкнут",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "Состояние насоса":   {"норма": "Выключен", "сработка": "Включен",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "Датчик движения":    {"норма": "Движения нет",
                           "сработка": "Обнаружено движение",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "КД Люк":             {"норма": "Норма", "сработка": "Не замкнут",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "КД Дверь":           {"норма": "Норма", "сработка": "Не замкнут",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "Газовый датчик":     {"норма": "Норма", "сработка": "Не замкнут",
                           "отказ": "Неисправен", "связь": "Неопределен"},
    "Состояние охраны":   {"норма": "На охране", "сработка": "Много неисправных устройств",
                           "отказ": "Много неисправных устройств", "связь": "Неопределен"},
}

SYSTEM_OF = {
    "Датчик дыма": "Пожарная охрана", "Тепловой датчик": "Пожарная охрана",
    "Ручной извещатель": "Пожарная охрана", "Датчик температуры": "Температурная подсистема",
    "Датчик затопления": "Диспетчерский контроль", "Состояние насоса": "Диспетчерский контроль",
    "Датчик движения": "Охранная подсистема", "КД Люк": "Охранная подсистема",
    "КД Дверь": "Охранная подсистема", "Состояние охраны": "Охранная подсистема",
    "Газовый датчик": "Газовая охрана",
}


def load_calibration(path=CALIBRATION):
    """Измеренные распределения. Без них генератор запускать нельзя."""
    if not os.path.exists(path):
        raise SystemExit(
            f"нет файла калибровки {path}.\n"
            "Соберите его: python -m scripts.11_calibrate_sensors")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def failure_rate(calibration, sensor_type, default=0.0005):
    """Суточная вероятность отказа прибора этого типа — из измерений."""
    for row in calibration["поведение_датчиков"]:
        if row["sensor_type"] == sensor_type and row.get("daily_failure_rate"):
            return float(row["daily_failure_rate"])
    return default


def activity(calibration, sensor_type):
    """Как часто прибор этого типа вообще выходит на связь и сколько говорит."""
    for row in calibration["поведение_датчиков"]:
        if row["sensor_type"] == sensor_type:
            return (float(row.get("active_share") or 0.1),
                    float(row.get("events_per_active_day") or 3.0))
    return 0.1, 3.0


# --------------------------------------------------------------- физика

def build_physics(calibration, days=540, pickets=120, seed=42):
    """Что произошло в коллекторе. Оснащение на это не влияет.

    Отказы базовых приборов вызревают: за `maturation` суток до поломки
    прибор начинает сбоить, и интенсивность сбоев растёт. Медиана вызревания
    взята из измерений по реальным вердиктам — 29 суток.

    Инциденты происходят в точках независимо от того, есть ли там датчик,
    способный их заметить. В этом вся суть: подтопление случается и там,
    где датчика затопления нет, просто мы об этом не узнаём.
    """
    rng = np.random.default_rng(seed)
    start = dt.date(2025, 1, 1)

    base_rate = failure_rate(calibration, "Датчик дыма")
    devices = []
    for picket in range(pickets):
        for kind in CORE_SENSORS:
            device = {"picket": picket, "sensor_type": kind,
                      "fail_day": None, "maturation": 0}
            # Один прибор может отказать не чаще одного раза за период:
            # после поломки его меняют, и это уже другая история.
            if rng.random() < base_rate * days:
                device["fail_day"] = int(rng.integers(40, days))
                device["maturation"] = int(max(1, rng.gamma(shape=2.2, scale=13)))
            devices.append(device)

    # Подтопление: сезонный процесс. Паводок марта даёт вчетверо больше
    # пусков насоса, чем сентябрь — это измерено.
    floods = []
    for _ in range(int(days / 45)):
        day = int(rng.integers(0, days))
        month = (start + dt.timedelta(days=day)).month
        season = {3: 2.6, 4: 1.9, 5: 1.3, 11: 1.2}.get(month, 1.0)
        floods.append({"day": day, "picket": int(rng.integers(0, pickets)),
                       "strength": float(rng.gamma(2.0, 1.4) * season),
                       "duration": int(rng.integers(1, 5))})

    # Задымление: локальный очаг. В реальных эпизодах срабатывает около
    # двух каналов и четыре раза — отсюда и параметры.
    fires = []
    for _ in range(int(days / 90)):
        fires.append({"day": int(rng.integers(0, days)),
                      "picket": int(rng.integers(0, pickets)),
                      "intensity": float(rng.gamma(2.0, 2.1)),
                      "heat_delta": float(rng.gamma(2.0, 9.0)),
                      "radius": int(rng.integers(1, 4))})

    # Проникновение: вскрытие люка ночью, движение внутри.
    intrusions = []
    for _ in range(int(days / 60)):
        intrusions.append({"day": int(rng.integers(0, days)),
                           "picket": int(rng.integers(0, pickets)),
                           "hour": int(rng.integers(0, 24)),
                           "minutes": int(rng.gamma(2.0, 48)),
                           "triggers": int(max(2, rng.gamma(2.0, 12)))})

    # Обрыв питающей линии участка. Это свойство сооружения, а не схемы
    # подключения: линия 48 В кормит отрезок трассы, и её обрыв обесточивает
    # всё, что на нём стоит. Насколько это будет видно — зависит от того,
    # сколько приборов там установлено и как они сгруппированы.
    power_cuts = []
    for _ in range(int(days / 70)):
        first = int(rng.integers(0, max(1, pickets - 12)))
        power_cuts.append({"day": int(rng.integers(0, days)),
                           "from": first, "to": first + int(rng.integers(4, 14)),
                           "hours": int(rng.integers(2, 30))})

    # Регламентные проверки сигнализации. Это работа бригады, а не событие
    # в коллекторе, но выглядит в журнале почти как настоящее задымление:
    # на реальных данных 72 % эпизодов дыма — именно прогоны ТО.
    sweeps = []
    for _ in range(int(days / 30)):
        first = int(rng.integers(0, max(1, pickets - 20)))
        sweeps.append({"day": int(rng.integers(0, days)),
                       "from": first, "to": first + int(rng.integers(10, 25))})

    return {"start": start, "days": days, "pickets": pickets, "seed": seed,
            "devices": devices, "floods": floods, "fires": fires,
            "intrusions": intrusions, "power_cuts": power_cuts,
            "sweeps": sweeps}


def ground_truth(physics):
    """Истина для метрик. В модель не попадает никогда."""
    truth = {"failures": [], "floods": [], "fires": [], "intrusions": [],
             "power_cuts": []}
    start = physics["start"]
    for index, device in enumerate(physics["devices"]):
        if device["fail_day"] is not None:
            truth["failures"].append({
                "picket": device["picket"],
                "sensor_type": device["sensor_type"],
                "day": (start + dt.timedelta(days=device["fail_day"])).isoformat(),
                "maturation": device["maturation"],
            })
    for key in ("floods", "fires", "intrusions"):
        for item in physics[key]:
            truth[key].append({
                "picket": item["picket"],
                "day": (start + dt.timedelta(days=item["day"])).isoformat(),
            })
    # Обрыв линии задевает отрезок, а не точку, поэтому границы сохраняются:
    # по ним потом сверяется, увидел ли сервис каскад целиком.
    for cut in physics["power_cuts"]:
        truth["power_cuts"].append({
            "from": cut["from"], "to": cut["to"], "hours": cut["hours"],
            "day": (start + dt.timedelta(days=cut["day"])).isoformat(),
        })
    return truth


# -------------------------------------------------------- наблюдаемость

# Базовые пороги срабатывания — то, что стоит на приборах сети сейчас.
# Вынесены в константу, потому что их показывает интерфейс: у конфигураций,
# посчитанных до появления этой оси, поля порогов в файле нет, и подставить
# нужно именно эти значения, а не выдуманные.
DEFAULT_THRESHOLDS = {"heat": 12.0, "flood": 2.5, "temperature": 3.0}

THRESHOLD_LABELS = {
    "heat": "тепловой датчик реагирует на превышение",
    # У притока воды в генераторе нет физической единицы: это гамма-величина,
    # задающая интенсивность и число пусков насоса. Называть её «метрами»
    # или «миллиметрами» было бы выдумкой, поэтому шкала названа условной
    # и сравнивать её значения осмысленно только между собой.
    "flood": "датчик затопления реагирует на приток силой",
    "temperature": "датчик температуры реагирует на отклонение",
}

THRESHOLD_UNITS = {"heat": "°C", "flood": "по условной шкале",
                   "temperature": "°C"}


def configuration(name, sensors, polling=None, self_diagnostics=True,
                  line_monitoring=False, controller_size=12, thresholds=None):
    """Описание оснащения объекта.

    `sensors` — шаг установки в пикетах: 1 значит в каждой точке, 4 —
    в каждой четвёртой. `polling` — интервал опроса в минутах; None означает
    событийный режим, когда прибор говорит, только если что-то изменилось.
    """
    return {
        "name": name,
        "sensors": dict(sensors),
        "polling": dict(polling or {}),
        "self_diagnostics": self_diagnostics,
        "line_monitoring": line_monitoring,
        "controller_size": controller_size,
        "thresholds": dict(thresholds or DEFAULT_THRESHOLDS),
    }


def channels_of(config_, pickets):
    """Какие каналы физически существуют при таком оснащении."""
    channels, index = [], 0
    for sensor_type, step in sorted(config_["sensors"].items()):
        for picket in range(0, pickets, max(1, int(step))):
            channels.append({
                "channel_id": CHANNEL_ID_BASE + index,
                "sensor_type": sensor_type,
                "picket": picket,
                "system": SYSTEM_OF.get(sensor_type, "Диспетчерский контроль"),
                # Группировка по контроллеру: от неё зависит, виден ли каскад.
                "base": f"exp{index // max(1, config_['controller_size'])}",
            })
            index += 1
    return channels


def _emit(rows, channel, day_index, start, minute, state, num=None):
    """Одна запись журнала того же вида, что в настоящих данных."""
    vocabulary = VALUES.get(channel["sensor_type"], VALUES["Датчик дыма"])
    raw = vocabulary.get(state, vocabulary["норма"])
    minute = int(min(max(minute, 0), 1439))
    rows.append({
        "event_id": len(rows) + 1,
        "channel_id": channel["channel_id"],
        "d": start + dt.timedelta(days=int(day_index)),
        "t": dt.time(minute // 60, minute % 60, int(len(rows) * 7 % 60)),
        "is_alarm": state in ("сработка", "отказ"),
        "raw_value": raw,
        "num_value": num,
    })


def observe(physics, config_, seed=None):
    """Журнал событий: что из физики попало в систему при таком оснащении.

    Здесь и только здесь оснащение влияет на результат. Физика на входе
    одна и та же для всех конфигураций, поэтому любая разница в метриках —
    следствие того, что поставили и как настроили.
    """
    rng = np.random.default_rng(physics["seed"] if seed is None else seed)
    start, days = physics["start"], physics["days"]
    channels = channels_of(config_, physics["pickets"])
    by_picket = {}
    for channel in channels:
        by_picket.setdefault(channel["picket"], []).append(channel)

    rows = []
    calibration = load_calibration()

    # --- фон: чем чаще опрос, тем регулярнее канал подтверждает, что жив
    for channel in channels:
        share, per_day = activity(calibration, channel["sensor_type"])
        interval = config_["polling"].get(channel["sensor_type"])
        if interval:
            share, per_day = 1.0, max(1.0, min(24 * 60 / interval, 48))
        failed_on = None
        for device in physics["devices"]:
            if (device["picket"] == channel["picket"]
                    and device["sensor_type"] == channel["sensor_type"]):
                failed_on = device["fail_day"]
        for day in range(days):
            # После отказа прибор молчит, пока его не заменят.
            if failed_on is not None and day > failed_on:
                continue
            if rng.random() > share:
                continue
            for _ in range(int(max(1, rng.poisson(per_day)))):
                _emit(rows, channel, day, start, int(rng.integers(0, 1440)), "норма")

    # --- отказы базовых приборов и их вызревание
    for device in physics["devices"]:
        if device["fail_day"] is None:
            continue
        here = [c for c in by_picket.get(device["picket"], [])
                if c["sensor_type"] == device["sensor_type"]]
        if not here:
            continue
        channel = here[0]
        fail_day, maturation = device["fail_day"], device["maturation"]

        for day in range(max(0, fail_day - maturation), fail_day + 1):
            closeness = 1.0 - (fail_day - day) / max(1, maturation)
            # Профиль взят из измерений: за сутки до отказа сообщение
            # о неисправности есть у 18,6 % каналов, за месяц — у 8,1 %.
            if config_["self_diagnostics"] and rng.random() < 0.08 + 0.11 * closeness:
                for _ in range(int(max(1, rng.poisson(1 + 6 * closeness)))):
                    _emit(rows, channel, day, start,
                          int(rng.integers(0, 1440)), "отказ")
            # Потеря связи видна только при контроле линии.
            if config_["line_monitoring"] and rng.random() < 0.28 + 0.07 * closeness:
                _emit(rows, channel, day, start,
                      int(rng.integers(0, 1440)), "связь")
        if config_["self_diagnostics"]:
            _emit(rows, channel, fail_day, start, int(rng.integers(0, 1440)), "отказ")

    # --- обрыв питающей линии: обесточивает отрезок целиком
    for cut in physics["power_cuts"]:
        affected = [c for c in channels if cut["from"] <= c["picket"] <= cut["to"]]
        for channel in affected:
            if config_["line_monitoring"]:
                _emit(rows, channel, cut["day"], start,
                      int(rng.integers(0, 1440)), "связь")
            elif config_["self_diagnostics"]:
                _emit(rows, channel, cut["day"], start,
                      int(rng.integers(0, 1440)), "отказ")

    # --- задымление: дым виден дымовым, жар — тепловым, если он есть
    for fire in physics["fires"]:
        for offset in range(-fire["radius"], fire["radius"] + 1):
            for channel in by_picket.get(fire["picket"] + offset, []):
                kind = channel["sensor_type"]
                decay = 1.0 / (1 + abs(offset))
                if kind == "Датчик дыма":
                    for _ in range(int(max(1, rng.poisson(fire["intensity"] * decay)))):
                        _emit(rows, channel, fire["day"], start,
                              int(rng.integers(0, 1440)), "сработка")
                elif kind == "Тепловой датчик":
                    if fire["heat_delta"] * decay >= config_["thresholds"]["heat"]:
                        _emit(rows, channel, fire["day"], start,
                              int(rng.integers(0, 1440)), "сработка")
                elif kind == "Датчик температуры":
                    interval = config_["polling"].get(kind)
                    # Редкий опрос просто не застаёт скачок температуры.
                    if interval and interval <= 60:
                        _emit(rows, channel, fire["day"], start,
                              int(rng.integers(0, 1440)), "сработка",
                              num=17.4 + fire["heat_delta"] * decay)

    # --- подтопление: пуски насоса и срабатывание датчика затопления
    for flood in physics["floods"]:
        for day in range(flood["day"], flood["day"] + flood["duration"]):
            for channel in by_picket.get(flood["picket"], []):
                kind = channel["sensor_type"]
                if kind == "Состояние насоса":
                    for _ in range(int(max(1, rng.poisson(3 + 6 * flood["strength"])))):
                        minute = int(rng.integers(0, 1430))
                        _emit(rows, channel, day, start, minute, "сработка")
                        _emit(rows, channel, day, start, minute + 3, "норма")
                elif kind == "Датчик затопления":
                    if flood["strength"] >= config_["thresholds"]["flood"]:
                        _emit(rows, channel, day, start,
                              int(rng.integers(0, 1440)), "сработка")

    # --- проникновение: вскрытие люка и движение внутри
    for event in physics["intrusions"]:
        for channel in by_picket.get(event["picket"], []):
            kind = channel["sensor_type"]
            if kind in ("КД Люк", "КД Дверь"):
                _emit(rows, channel, event["day"], start, event["hour"] * 60,
                      "сработка")
            elif kind == "Датчик движения":
                for i in range(int(min(event["triggers"], 60))):
                    _emit(rows, channel, event["day"], start,
                          event["hour"] * 60 + i * 2, "сработка")

    # --- прогоны ТО: срабатывает всё, что стоит на отрезке
    for sweep in physics["sweeps"]:
        for picket in range(sweep["from"], sweep["to"] + 1):
            for channel in by_picket.get(picket, []):
                if channel["sensor_type"] in ("Датчик дыма", "Тепловой датчик",
                                              "Ручной извещатель"):
                    _emit(rows, channel, sweep["day"], start,
                          int(rng.integers(540, 1020)), "сработка")

    # --- ложные срабатывания: свойство самого прибора, а не коллектора.
    # Чем больше датчиков поставлено, тем больше ложных тревог — и это
    # обратная сторона дооснащения, которую эксперимент обязан показать.
    FALSE_RATE = {"Датчик дыма": 0.0012, "Тепловой датчик": 0.0004,
                  "Датчик движения": 0.004, "КД Люк": 0.0008,
                  "КД Дверь": 0.0010, "Датчик затопления": 0.0006,
                  "Газовый датчик": 0.0030}
    for channel in channels:
        rate = FALSE_RATE.get(channel["sensor_type"], 0.0008)
        for day in range(days):
            if rng.random() < rate:
                _emit(rows, channel, day, start,
                      int(rng.integers(0, 1440)), "сработка")

    rows.sort(key=lambda r: (r["d"], r["t"]))
    for number, row in enumerate(rows, 1):
        row["event_id"] = number
    return channels, rows


# ------------------------------------------- укладка в базу

# Порция вставки журнала. Два миллиона строк — это около 400 МБ кадра,
# и при лимите 16 ГБ запас остаётся даже когда рядом работает DuckDB.
JOURNAL_CHUNK = 2_000_000


def stage(con, channels, journal):
    """Кладёт синтетику в базу ровно в том виде, в каком её ждёт настоящий ETL."""
    con.execute("DROP TABLE IF EXISTS journal")
    con.execute("""
        CREATE TABLE journal (event_id BIGINT, channel_id BIGINT, d DATE,
                              t TIME, is_alarm BOOLEAN, raw_value VARCHAR,
                              num_value DOUBLE)
    """)
    # Вставка порциями через DataFrame, а не executemany. Измерено:
    # executemany кладёт миллион строк за 94,7 с, тот же миллион через
    # DataFrame — за 0,7 с, то есть в 129 раз быстрее. На конфигурации
    # с восемью типами датчиков и опросом раз в 15 минут журнал даёт
    # около 19 млн записей на прогон, и разница превращает полчаса
    # ожидания в полтора десятка секунд. Для конкурсной работы это
    # существенно: эксперимент должен быть воспроизводим за обозримое
    # время, иначе его никто не перепроверит.
    #
    # Порциями, а не целиком, чтобы не держать весь кадр в памяти:
    # ограничение машины — 16 ГБ.
    for start in range(0, len(journal), JOURNAL_CHUNK):
        frame = pd.DataFrame(journal[start:start + JOURNAL_CHUNK],
                             columns=["event_id", "channel_id", "d", "t",
                                      "is_alarm", "raw_value", "num_value"])
        con.register("journal_chunk", frame)
        con.execute("INSERT INTO journal SELECT event_id, channel_id, d, t, "
                    "is_alarm, raw_value, num_value FROM journal_chunk")
        con.unregister("journal_chunk")

    con.execute("DROP TABLE IF EXISTS dim_channel")
    con.execute("""
        CREATE TABLE dim_channel (
            channel_id BIGINT, system VARCHAR, sensor_type VARCHAR,
            sensor_name VARCHAR, tag VARCHAR, object_id BIGINT,
            tag_base VARCHAR, picket INTEGER, object_name VARCHAR,
            object_kind VARCHAR, parent_id BIGINT, parent_name VARCHAR,
            is_zone_aggregate BOOLEAN, is_power_type BOOLEAN,
            twin_count BIGINT, twin_group BIGINT)
    """)
    con.executemany(
        "INSERT INTO dim_channel VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(c["channel_id"], c["system"], c["sensor_type"],
          f"{c['sensor_type']} ЭКСП ПК{c['picket']}",
          f"exp/{c['picket']}/{c['channel_id']}",
          EXPERIMENT_OBJECT_ID, c["base"], c["picket"],
          EXPERIMENT_OBJECT_NAME, "синтетический",
          EXPERIMENT_PARENT_ID, EXPERIMENT_OBJECT_NAME,
          False, False, 1, c["channel_id"]) for c in channels])

    con.execute("DROP TABLE IF EXISTS dim_value_class")
    con.execute("CREATE TABLE dim_value_class (sensor_type VARCHAR, "
                "raw_value VARCHAR, cls VARCHAR)")
    con.executemany("INSERT INTO dim_value_class VALUES (?, ?, ?)",
                    [(st, val, cls)
                     for (st, val), cls in contract.load_contract().items()])

    # Профиль канала: возраст прибора берётся отсюда. Строится тем же
    # способом, что и на реальных данных — по первому появлению в журнале.
    con.execute("DROP TABLE IF EXISTS channel_profile")
    con.execute("""
        CREATE TABLE channel_profile AS
        SELECT channel_id, min(d) AS first_day, max(d) AS last_day,
               count(DISTINCT d) AS active_days, count(*) AS total_ev,
               median(1) AS median_ev_per_active_day,
               quantile_cont(1, 0.95) AS p95_ev_per_active_day,
               date_diff('day', min(d), max(d)) + 1 AS lifespan_days,
               count(DISTINCT d) * 1.0
                   / nullif(date_diff('day', min(d), max(d)) + 1, 0) AS active_share,
               (date_diff('day', min(d), max(d)) + 1.0)
                   / nullif(count(DISTINCT d), 0) AS mean_gap_days
        FROM journal GROUP BY channel_id
    """)

    # Логические домены — те же, что строит настоящий конвейер:
    # сколько каналов и типов стоит в точке.
    con.execute("DROP TABLE IF EXISTS logical_domain")
    con.execute("""
        CREATE TABLE logical_domain AS
        SELECT object_id, picket,
               count(*)                       AS n_channels,
               count(DISTINCT sensor_type)    AS n_sensor_types,
               count(DISTINCT system)         AS n_systems,
               string_agg(DISTINCT sensor_type, ', ') AS sensor_types,
               string_agg(channel_id::VARCHAR, ',')   AS channel_ids,
               any_value(object_name)         AS object_name,
               any_value(parent_name)         AS parent_name,
               max(CASE WHEN sensor_type = 'Тепловой датчик' THEN true ELSE false END) AS has_thermal,
               max(CASE WHEN sensor_type = 'Датчик дыма' THEN true ELSE false END)     AS has_smoke,
               max(CASE WHEN sensor_type = 'Состояние насоса' THEN true ELSE false END) AS has_pump,
               max(CASE WHEN sensor_type IN ('Датчик движения','КД Люк','КД Дверь')
                        THEN true ELSE false END) AS has_intrusion
        FROM dim_channel GROUP BY object_id, picket
    """)


def build_labels(con, physics, channels):
    """Целевая переменная из физики, а не из наблюдений.

    Это единственное место, где синтетика отличается от реальности, и так
    и должно быть: иначе конфигурация, в которой приборы молчат о своей
    неисправности, не дала бы ни одного положительного примера, и сравнивать
    стало бы нечего. Истина одна для всех конфигураций.
    """
    core = {(c["picket"], c["sensor_type"]): c["channel_id"]
            for c in channels if c["sensor_type"] in CORE_SENSORS}
    fail_day = {}
    for device in physics["devices"]:
        key = (device["picket"], device["sensor_type"])
        if device["fail_day"] is not None and key in core:
            fail_day[core[key]] = device["fail_day"]

    start = physics["start"]
    rows = []
    for channel_id in sorted(core.values()):
        limit = fail_day.get(channel_id, physics["days"])
        for day in range(physics["days"]):
            if day > limit:
                continue       # после отказа прибора сутки не считаются
            y = 1 if fail_day.get(channel_id) == day + 1 else 0
            rows.append((channel_id, start + dt.timedelta(days=day), y))

    con.execute("DROP TABLE IF EXISTS labels")
    con.execute("CREATE TABLE labels (channel_id BIGINT, d DATE, y INTEGER)")
    con.executemany("INSERT INTO labels VALUES (?, ?, ?)", rows)
    return sum(r[2] for r in rows), len(rows)
