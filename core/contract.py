"""Семантический контракт значений.

Словарь выглядит как 49 942 уникальных значения, но это ложная сложность:
32 текстовых состояния покрывают 28,17 % объёма, числа — 71,79 %, остальное мусор.
Контракт сводит пару (тип датчика, сырое значение) к семантическому классу,
а класс — к триту. Покрытие проверено на всех 313 546 016 событиях.

Таблица лежит в core/value_contract.csv и является единственным источником истины:
и Python, и SQL-выражение для DuckDB собираются из неё.
"""

import csv
import re

from core import config
from core.trits import DEVIATION, NORMAL, UNKNOWN

# --------------------------------------------- классы и их смысл

NORM = "НОРМА"
TRIGGER = "СРАБОТКА"
FAULT = "ОТКАЗ"
DISABLED = "ОТКЛЮЧЕНО"
UNDEFINED = "НЕОПРЕДЕЛЕНО"
GARBAGE = "МУСОР"
NUMERIC = "ТЕЛЕМЕТРИЯ_ЧИСЛО"

# Класс → трит. Числовая телеметрия трита не имеет: её состояние определяется
# отклонением от персональной нормы канала, а не самим значением (см. trits.py).
CLASS_TO_TRIT = {
    NORM: NORMAL,
    TRIGGER: DEVIATION,
    FAULT: DEVIATION,
    DISABLED: UNKNOWN,     # устройство отключено — наблюдаемости нет
    UNDEFINED: UNKNOWN,    # связь потеряна — «не знаю», а не «норма»
    GARBAGE: UNKNOWN,
    NUMERIC: None,
}

# Классы, из которых строится целевая переменная модели отказов.
# «Неопределен» сюда НЕ входит: как предиктор жёсткого отказа он даёт lift
# всего 1,28x, то есть почти не отличается от фона (см. docs/01, разд. 2).
FAILURE_CLASSES = (FAULT, DISABLED)

# Типы каналов, которые относятся к состоянию энергоснабжения, а не к исправности
# датчика. Они дают 76,9 % позитивов таргета и перекосили бы модель, поэтому
# выносятся в отдельную задачу (см. docs/02, вопрос 14).
POWER_TYPES = (
    "Состояние фазы",
    "Состояние насоса",
    "Состояние вентилятора",
    "ИБП",
    "Состояние охраны",
)

# Агрегирующие псевдоканалы уровня зоны: меняются одновременно с инцидентом
# на своих датчиках, поэтому в признаках дают утечку таргета.
ZONE_AGGREGATE_MARKERS = ("зона", "[Охранная зона]")


# В CSV числа и даты свёрнуты в плейсхолдеры: по литералу их не найти, потому что
# в журнале лежат сами значения. Такие строки в справочник не кладём — числа
# определяются приведением и диапазоном, даты в поле значения — шаблоном.
PLACEHOLDERS = ("<ЧИСЛО>", "<ДАТА-ВРЕМЯ В ЗНАЧЕНИИ>")

_DATETIME_IN_VALUE = re.compile(r"^\d{2}\.\d{2}\.\d{4}[ T]\d{2}:\d{2}:\d{2}$")


def load_contract(path=None):
    """Читает контракт в словарь {(тип_датчика, значение): класс}.

    Плейсхолдеры пропускаются: они описывают шаблон, а не конкретное значение.
    """
    path = path or config.CONTRACT_CSV
    table = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            value = row["значение"]
            if value in PLACEHOLDERS:
                continue
            table[(row["тип_датчика"], value)] = row["семантический_класс"]
    return table


def parse_number(raw_value):
    """Число из сырого значения или None. Десятичная запятая допускается."""
    try:
        return float(str(raw_value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def is_sentinel(number):
    """Код ошибки датчика вместо показания.

    Производитель ставит заглушку (-127 у DS18B20, 999, -3276) вместо измерения.
    Такое значение нельзя ни усреднять, ни считать скачком: разница между -127
    и +18 даёт мнимый рост на 145 градусов и лавину ложных «пожаров».
    """
    if number is None:
        return False
    if number in config.TEMP_SENTINELS:
        return True
    low, high = config.TEMP_VALID_RANGE
    return not (low <= number <= high)


def classify(sensor_type, raw_value, table=None):
    """Класс для одного значения. Неизвестная пара даёт МУСОР, а не падение.

    Незнакомое значение — это всегда возможный новый тип события после смены
    прошивки, поэтому оно попадает в UNKNOWN-трит, а не приравнивается к норме.
    """
    table = table if table is not None else load_contract()
    found = table.get((sensor_type, raw_value))
    if found is not None:
        return found

    number = parse_number(raw_value)
    if number is not None:
        # Сентинел — это отсутствие измерения, а не измерение.
        return GARBAGE if is_sentinel(number) else NUMERIC

    text = str(raw_value)
    if text.startswith(config.EPOCH_GARBAGE_PREFIX) or _DATETIME_IN_VALUE.match(text):
        return GARBAGE
    return GARBAGE


def trit_of_class(semantic_class):
    """Трит по классу. Для числовой телеметрии возвращает None."""
    return CLASS_TO_TRIT.get(semantic_class, UNKNOWN)


def build_sql_case(table=None, type_col="тип_датчика", value_col="raw_value"):
    """Собирает CASE-выражение DuckDB из той же таблицы.

    Нужно, чтобы контракт применялся к 313 млн строк внутри SQL, а не построчно
    в Python. Логика при этом остаётся одна — расхождение между Python и SQL
    невозможно, потому что оба читают один CSV и один диапазон из config.

    Порядок ветвей значим: сначала отсекаются сентинелы и мусорные даты,
    и только потом значение считается настоящим измерением.
    """
    table = table if table is not None else load_contract()
    by_class = {}
    for (sensor_type, value), cls in table.items():
        by_class.setdefault(cls, []).append((sensor_type, value))

    def lit(s):
        return "'" + str(s).replace("'", "''") + "'"

    low, high = config.TEMP_VALID_RANGE
    sentinels = ", ".join(str(v) for v in config.TEMP_SENTINELS)
    num = f"TRY_CAST(replace({value_col}, ',', '.') AS DOUBLE)"

    parts = [
        # сломанный timestamp в поле значения — 83 054 события
        f"WHEN {value_col} LIKE {lit(config.EPOCH_GARBAGE_PREFIX + '%')} THEN {lit(GARBAGE)}",
        # код ошибки датчика вместо показания
        f"WHEN {num} IN ({sentinels}) THEN {lit(GARBAGE)}",
        f"WHEN {num} IS NOT NULL AND ({num} < {low} OR {num} > {high}) THEN {lit(GARBAGE)}",
    ]
    for cls, pairs in by_class.items():
        conds = " OR ".join(
            f"({type_col} = {lit(t)} AND {value_col} = {lit(v)})" for t, v in pairs
        )
        parts.append(f"WHEN {conds} THEN {lit(cls)}")
    parts.append(f"WHEN {num} IS NOT NULL THEN {lit(NUMERIC)}")

    return "CASE\n  " + "\n  ".join(parts) + f"\n  ELSE {lit(GARBAGE)}\nEND"


def build_sql_trit(class_expr):
    """CASE-выражение «класс → трит» поверх выражения класса."""
    branches = []
    for cls, trit in CLASS_TO_TRIT.items():
        if trit is None:
            continue
        branches.append(f"WHEN {class_expr} = '{cls}' THEN {trit}")
    return "CASE\n  " + "\n  ".join(branches) + f"\n  ELSE {UNKNOWN}\nEND"
