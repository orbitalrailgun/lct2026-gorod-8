"""Календарные факторы риска.

Заказчик назвал их прямо на встрече, говоря о нарушителях: «нервы у людей мы
не знаем, поэтому факторы риска вообще непрогнозируемы. Но есть вполне
характерные календарные точки: наши праздничные дни, большие длинные
праздничные дни. Есть особые праздники, например, 9 мая».

В таблице ответов календарные риски названы среди ожидаемого содержания
карточки прогноза наравне с сезонностью и температурными тенденциями.

Механика двойная и в этом суть:

* объект в праздники чаще остаётся без присмотра — растёт риск проникновения;
* бригад в эти дни меньше — растёт время реакции на любой вердикт,
  поэтому заявку разумнее закрыть до начала длинных выходных.

Ограничение, которое проговаривается честно: используется фиксированный набор
нерабочих дней из Трудового кодекса. Ежегодные переносы рабочих дней,
утверждаемые постановлением Правительства, здесь не учитываются — они меняются
каждый год, а официального производственного календаря в данных нет.
"""

import datetime as dt

# Нерабочие праздничные дни по статье 112 ТК РФ. Набор фиксированный;
# переносы, устанавливаемые ежегодно, сюда не входят.
FIXED_HOLIDAYS = {
    (1, 1): "Новый год",
    (1, 2): "Новогодние каникулы",
    (1, 3): "Новогодние каникулы",
    (1, 4): "Новогодние каникулы",
    (1, 5): "Новогодние каникулы",
    (1, 6): "Новогодние каникулы",
    (1, 7): "Рождество Христово",
    (1, 8): "Новогодние каникулы",
    (2, 23): "День защитника Отечества",
    (3, 8): "Международный женский день",
    (5, 1): "Праздник Весны и Труда",
    (5, 9): "День Победы",
    (6, 12): "День России",
    (11, 4): "День народного единства",
}

# Дни, которые заказчик выделил отдельно как точки повышенного риска.
SPECIAL = {(5, 9): "День Победы"}

# Длинные выходные: столько нерабочих дней подряд считаются периодом
# пониженного присутствия персонала на объектах.
LONG_BREAK_MIN = 3


def is_holiday(day):
    """Нерабочий праздничный день."""
    return (day.month, day.day) in FIXED_HOLIDAYS


def is_weekend(day):
    return day.weekday() >= 5


def is_nonworking(day):
    return is_holiday(day) or is_weekend(day)


def holiday_name(day):
    return FIXED_HOLIDAYS.get((day.month, day.day))


def break_length(day):
    """Длина непрерывного нерабочего периода, в который попадает дата."""
    if not is_nonworking(day):
        return 0
    length = 1
    back = day - dt.timedelta(days=1)
    while is_nonworking(back):
        length += 1
        back -= dt.timedelta(days=1)
    forward = day + dt.timedelta(days=1)
    while is_nonworking(forward):
        length += 1
        forward += dt.timedelta(days=1)
    return length


def days_to_next_break(day, horizon=14):
    """Через сколько суток начинается ближайший длинный нерабочий период.

    Нужно для срока заявки: работу лучше закрыть до того, как объект
    останется без присмотра, а бригад станет меньше.
    """
    for offset in range(horizon + 1):
        probe = day + dt.timedelta(days=offset)
        if is_nonworking(probe) and break_length(probe) >= LONG_BREAK_MIN:
            return offset
    return None


def risk_factor(day):
    """Календарный фактор риска для карточки вердикта.

    Возвращает множитель для охранных сценариев и готовую формулировку.
    Множитель умеренный: календарь меняет вероятность присутствия людей
    на объекте, но не создаёт инцидент сам по себе.
    """
    if isinstance(day, str):
        day = dt.date.fromisoformat(day[:10])

    name = holiday_name(day)
    length = break_length(day)

    if (day.month, day.day) in SPECIAL:
        return 1.35, (f"{SPECIAL[(day.month, day.day)]} — объекты без постоянного "
                      "присмотра, бригад на линии меньше обычного")
    if name and length >= LONG_BREAK_MIN:
        return 1.30, (f"{name}, {length}-й день длинных выходных — "
                      "присутствие персонала на объектах пониженное")
    if name:
        return 1.20, f"{name} — нерабочий день, персонала на объектах меньше"
    if is_weekend(day) and length >= LONG_BREAK_MIN:
        return 1.15, f"длинные выходные ({length} нерабочих дня подряд)"
    if is_weekend(day):
        return 1.10, "выходной день — плановых работ на объекте не ведётся"

    upcoming = days_to_next_break(day)
    if upcoming is not None and upcoming <= 3:
        if upcoming == 0:
            return 1.0, None
        return 1.0, (f"через {upcoming} сут начинаются длинные выходные — "
                     "работы разумно закрыть до их начала")
    return 1.0, None


def adjust_due_date(due_date, day):
    """Сдвигает срок заявки на день раньше начала длинных выходных.

    Если работа формально может подождать, но её срок приходится на период,
    когда бригад меньше, это скрытый риск: заявка провисит все выходные.
    """
    if isinstance(day, str):
        day = dt.date.fromisoformat(day[:10])
    if isinstance(due_date, str):
        due_date = dt.date.fromisoformat(due_date[:10])

    probe = day
    while probe <= due_date:
        if is_nonworking(probe) and break_length(probe) >= LONG_BREAK_MIN:
            shifted = probe - dt.timedelta(days=1)
            return (max(shifted, day),
                    f"срок сдвинут на {shifted:%d.%m}: далее следуют "
                    f"{break_length(probe)} нерабочих дня подряд")
        probe += dt.timedelta(days=1)
    return due_date, None
