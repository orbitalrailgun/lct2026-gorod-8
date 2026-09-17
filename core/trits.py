"""Троичная логика состояний.

Состояние канала имеет три значения, а не два:

    +1  ОТКЛОНЕНИЕ  — сработка, отказ, выход числа за персональную норму
     0  НЕИЗВЕСТНО  — нет связи, канал молчит, датчика нет, канал без паспорта
    -1  НОРМА       — наблюдаем, всё в порядке

Третье значение не вспомогательное. В данных 7,5 млн событий «Неопределен»,
тепловых датчиков нет на 13 объектах из 16, датчиков затопления — на 8 из 16,
и 1 142 канала вовсе без паспорта. Бинарная кодировка обязана округлить всё это
до нормы, и именно так сервис начинает сообщать диспетчеру ложное спокойствие.

Операции — сильная логика Клини на порядке -1 < 0 < +1. Правило «логических
доменов», которое сформулировал заказчик, записывается через confirm():

    confirm(+1, -1) = -1   сосед в норме, одиночную тревогу не засчитываем
    confirm(+1,  0) =  0   соседа нет или он не наблюдаем: «не знаю», а не «нет»

Второй случай бинарная логика обязана свести к «нет» — здесь он сохраняется.
"""

import numpy as np

# ------------------------------------------------------------ значения

DEVIATION = 1
UNKNOWN = 0
NORMAL = -1

TRIT_NAMES = {
    DEVIATION: "отклонение",
    UNKNOWN: "неизвестно",
    NORMAL: "норма",
}

# Формулировки для карточки вердикта. Важно, что «неизвестно» звучит как
# признание границы наблюдаемости, а не как успокоение.
TRIT_PHRASES = {
    DEVIATION: "зафиксировано отклонение",
    UNKNOWN: "нет данных — подтвердить нечем",
    NORMAL: "в пределах нормы",
}


# ------------------------------------------------------------ операции

def confirm(a, b):
    """Подтверждение: отклонение засчитывается, только если его поддержал сосед.

    Это и есть «логический домен» заказчика. min() по порядку -1 < 0 < +1.
    Работает и на скалярах, и на массивах numpy.
    """
    return np.minimum(a, b)


def escalate(a, b):
    """Эскалация: достаточно любого источника. max() по тому же порядку."""
    return np.maximum(a, b)


def negate(a):
    """Отрицание: норма и отклонение меняются местами, неизвестность сохраняется."""
    return -np.asarray(a) if isinstance(a, (list, np.ndarray)) else -a


def confirm_all(trits, axis=None):
    """Подтверждение по группе каналов сразу (логический домен из 3+ элементов)."""
    return np.min(np.asarray(trits), axis=axis)


def escalate_any(trits, axis=None):
    """Эскалация по группе: сработал хоть кто-то."""
    return np.max(np.asarray(trits), axis=axis)


def is_known(a):
    """Есть ли наблюдаемость. Нужно, чтобы отделить «нормально» от «не знаю»."""
    return np.asarray(a) != UNKNOWN


def observability(trits, axis=None):
    """Доля наблюдаемых каналов в группе — от 0.0 (полностью слепы) до 1.0.

    Показывается диспетчеру рядом с вердиктом: вердикт по группе, где наблюдаемы
    2 канала из 10, и вердикт по полностью наблюдаемой группе — разные по весу.
    """
    arr = np.asarray(trits)
    n = arr.shape[axis] if axis is not None else arr.size
    if n == 0:
        return 0.0
    return np.sum(arr != UNKNOWN, axis=axis) / n


# ------------------------------------------- перевод чисел в триты

def trit_from_zscore(z, threshold=2.0):
    """Числовая телеметрия в трит по устойчивому z-score относительно личной нормы.

    NaN означает отсутствие свежего замера, а не норму, поэтому даёт UNKNOWN.
    Порог двусторонний: и аномальный рост, и аномальное падение — отклонение
    (у кислорода тревога именно на падение).
    """
    z = np.asarray(z, dtype=float)
    out = np.full(z.shape, NORMAL, dtype=np.int8)
    out[np.abs(z) >= threshold] = DEVIATION
    out[np.isnan(z)] = UNKNOWN
    return out


def trit_from_staleness(age_hours, expected_interval_hours, factor=10.0):
    """Молчание канала в трит.

    Канал, молчащий дольше своего обычного интервала в `factor` раз, считается
    неисправным (DEVIATION), а не спокойным. Отсутствие истории интервала даёт
    UNKNOWN: мы не знаем, что для этого канала нормально.
    """
    age = np.asarray(age_hours, dtype=float)
    exp = np.asarray(expected_interval_hours, dtype=float)
    out = np.full(age.shape, NORMAL, dtype=np.int8)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[age > exp * factor] = DEVIATION
    out[np.isnan(age) | np.isnan(exp) | (exp <= 0)] = UNKNOWN
    return out


# ------------------------------------------------- пояснение вердикта

def explain_confirmation(name_a, trit_a, name_b, trit_b):
    """Человеческая формулировка результата confirm() — для карточки вердикта.

    Текст детерминирован и собирается подстановкой, а не генерируется,
    поэтому вердикт не может разойтись с данными.
    """
    result = int(confirm(trit_a, trit_b))
    if result == DEVIATION:
        return f"подтверждено: {name_a} и {name_b} одновременно показывают отклонение"
    if result == UNKNOWN:
        unknown = name_b if trit_b == UNKNOWN else name_a
        other = name_a if trit_b == UNKNOWN else name_b
        return (f"подтвердить нечем: {other} показывает отклонение, "
                f"но {unknown} недоступен — это не значит, что всё в порядке")
    if trit_a == DEVIATION or trit_b == DEVIATION:
        quiet = name_b if trit_b == NORMAL else name_a
        loud = name_a if trit_b == NORMAL else name_b
        return f"не подтверждено: {loud} сработал, но {quiet} в норме — вероятно, одиночный шум"
    return f"норма: {name_a} и {name_b} в пределах нормы"
