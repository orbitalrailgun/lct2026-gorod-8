"""Черновики заявок на ремонт.

Организаторы назвали это прямо: «достаточно создания черновика или шаблона»,
реальная отправка во внешние системы не требуется. Журнал ОДС ведётся
в отдельной системе на Django и интегрирован не будет, поэтому заявка
формируется у нас и выгружается в XLSX.

Главный принцип: **обоснование не пишется руками, а собирается из карточки
вердикта**. Если бы диспетчер набирал его заново, заявка со временем начала бы
расходиться с данными, на которых построен прогноз, и потеряла бы доказательную
силу. Здесь обоснование — это те же улики с теми же числами и метками времени.

Срок и приоритет выводятся из вызревания признаков. Разница принципиальная
и была измерена на данных: медиана вызревания 33 суток. Постепенная деградация
закрывается плановой заявкой, внезапное развитие требует выезда — и это разные
сроки, а не разные слова.
"""

import datetime as dt

from core import calendar_risk

# Тип работ по сценарию и оборудованию. Таблица, а не логика в коде:
# формулировки правит команда эксплуатации, не разработчик.
WORK_TYPES = {
    ("отказ", "Датчик дыма"): "Проверка шлейфа пожарной сигнализации",
    ("отказ", "Тепловой датчик"): "Проверка теплового извещателя",
    ("отказ", "Датчик температуры"): "Проверка температурного датчика и линии связи",
    ("отказ", "Газовый датчик"): "Проверка и калибровка газоанализатора",
    ("отказ", "Датчик движения"): "Проверка объёмного извещателя",
    ("отказ", "КД Дверь"): "Проверка дверного контакта и шлейфа",
    ("отказ", "КД Люк"): "Проверка контакта люка",
    ("отказ", "КД АВ"): "Проверка контакта АВ",
    ("отказ", None): "Проверка канала и линии связи",
    ("подтопление", None): "Обследование приямка и насосного оборудования",
    ("пожар", None): "Визуальный осмотр участка, проверка пожарной сигнализации",
    ("проникновение", None): "Осмотр люков и дверей участка, проверка охранного контура",
}

PRIORITIES = ("низкий", "средний", "высокий", "аварийный")

# Кто исполняет — по сценарию. Заказчик пояснил, что отдельной роли групп
# реагирования у него нет и дежурные бригады работают под ролью диспетчера,
# но ментор предложил такую роль выделить, и для заявки она осмысленна.
RESPONSIBLE = {
    "отказ": "Служба эксплуатации КИПиА",
    "подтопление": "Служба водоудаления",
    "пожар": "Дежурная бригада",
    "проникновение": "Группа реагирования",
}


def work_type(scenario, sensor_type=None):
    return (WORK_TYPES.get((scenario, sensor_type))
            or WORK_TYPES.get((scenario, None))
            or "Обследование оборудования")


def derive_priority(probability, scenario, incubation_days=None, cascade=False):
    """Приоритет из вероятности, характера развития и масштаба.

    Каскад по питающей линии повышает приоритет независимо от вероятности:
    отказ луча обесточивает десятки точек сразу, и ждать планового обхода нельзя.
    """
    if cascade:
        return "аварийный"
    if scenario in ("пожар", "проникновение") and probability >= 0.6:
        return "аварийный"
    if probability >= 0.85:
        return "высокий"
    if probability >= 0.6:
        return "средний" if (incubation_days or 0) > 7 else "высокий"
    return "низкий"


def derive_due_date(day, priority, incubation_days=None):
    """Срок выполнения.

    Вызревание признаков переводится в срок напрямую: то, что копилось месяц,
    не обязано чиниться завтра, а то, что возникло за час, не может ждать обхода.
    """
    base = dt.date.fromisoformat(str(day)[:10]) if isinstance(day, str) else day
    if priority == "аварийный":
        return base + dt.timedelta(days=1)
    if priority == "высокий":
        return base + dt.timedelta(days=3)
    if priority == "средний":
        return base + dt.timedelta(days=10)
    return base + dt.timedelta(days=30)


def build_justification(card):
    """Обоснование из карточки вердикта — теми же словами и числами.

    Сохраняются метки времени: заявка должна отвечать не только «почему»,
    но и «с какого момента», иначе бригада не поймёт, срочно это или нет.
    """
    lines = []
    for item in card.get("evidence", []):
        stamp = str(item.get("since") or "")
        prefix = f"{stamp[:19].replace('T', ' ')} — " if len(stamp) > 10 else ""
        lines.append(f"{prefix}{item['phrase']}")

    if card.get("blind_spots"):
        lines.append("")
        lines.append("Ограничения наблюдаемости:")
        lines.extend(f"— {b}" for b in card["blind_spots"])

    if card.get("precedent"):
        lines.append("")
        lines.append(card["precedent"])
    return "\n".join(lines)


def build_draft(verdict, day, cascade=False, author=None):
    """Черновик заявки из вердикта. Все поля выводятся, ничего не выдумывается."""
    card = verdict.get("card", {})
    probability = verdict.get("probability", 0.0)
    incubation = card.get("incubation_days")

    priority = derive_priority(probability, verdict.get("scenario"),
                               incubation, cascade)
    due = derive_due_date(day, priority, incubation)
    # Срок сдвигается перед длинными выходными: заявка, попавшая на период
    # с уменьшенным числом бригад, провисит все нерабочие дни.
    due, calendar_note = calendar_risk.adjust_due_date(due, day)
    return {
        "verdict_id": verdict.get("id"),
        "object_name": verdict.get("object_name"),
        "picket": verdict.get("picket"),
        "work_type": work_type(verdict.get("scenario"), card.get("sensor_type")),
        "priority": priority,
        "due_date": due,
        "calendar_note": calendar_note,
        "responsible": RESPONSIBLE.get(verdict.get("scenario"), "Служба эксплуатации"),
        "justification": build_justification(card),
        "recommendation": card.get("recommendation"),
        "title": card.get("title"),
        "probability": probability,
        "incubation_days": incubation,
        "author": author,
        "status": "черновик",
    }


def summary_line(draft):
    """Одна строка для списка заявок."""
    where = draft["object_name"] or ""
    if draft.get("picket") is not None:
        where += f", ПК{draft['picket']}"
    line = (f"{draft['work_type']} — {where}. "
            f"Приоритет {draft['priority']}, срок до {draft['due_date']:%d.%m.%Y}, "
            f"исполнитель: {draft['responsible']}.")
    if draft.get("calendar_note"):
        line += f" {draft['calendar_note'].capitalize()}."
    return line
