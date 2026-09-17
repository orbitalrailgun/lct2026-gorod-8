"""Веб-интерфейс диспетчера.

Главный экран — не график и не карта, а лента вердиктов. Диспетчер работает
очередью: что проверить в первую очередь и почему. Каждая строка раскрывается
в карточку с обоснованием.

Квитирование сделано буквально так, как описал заказчик: неотработанный вердикт
визуально выделен и остаётся в ленте, пока диспетчер его не закрыл. В системе
ОЭК тот же принцип реализован миганием до квитирования.
"""

import json
import os

from nicegui import app, ui

from core import config, db, explain

SEED = os.path.join(config.ROOT, "deploy", "seed_verdicts.json")

STATE = {"user_id": 3, "role": "диспетчер_одс", "scenario": None, "status": None}

SCENARIO_COLORS = {
    "отказ": "orange",
    "подтопление": "blue",
    "пожар": "red",
    "проникновение": "purple",
}


# ----------------------------------------------------------------- данные

def get_con():
    """Соединение с оперативным контуром или None, если БД недоступна."""
    try:
        return db.connect()
    except Exception:
        return None


def load_verdicts(limit=200):
    """Вердикты из БД, а без неё — из seed-файла.

    Запасной путь нужен, чтобы интерфейс запускался и без PostgreSQL:
    это упрощает отладку и снимает риск пустого экрана на демонстрации.
    """
    con = get_con()
    if con is not None:
        try:
            rows = db.fetch_verdicts(con, status=STATE["status"],
                                     scenario=STATE["scenario"], limit=limit)
            if rows:
                return rows, True
        except Exception:
            pass
    if os.path.exists(SEED):
        with open(SEED, encoding="utf-8") as fh:
            raw = json.load(fh)
        rows = [{
            "id": i + 1, "created_at": None, "scenario": r["scenario"],
            "object_name": r["object_name"], "picket": r["picket"],
            "channel_id": r["channel_id"], "probability": r["probability"],
            "horizon_hours": r["horizon_hours"], "status": "новый", "card": r["card"],
        } for i, r in enumerate(raw)]
        if STATE["scenario"]:
            rows = [r for r in rows if r["scenario"] == STATE["scenario"]]
        return rows, False
    return [], False


def risk_color(p):
    return "red" if p >= 0.8 else "orange" if p >= 0.5 else "grey"


# ----------------------------------------------------------------- вид

def header():
    with ui.header().classes("items-center justify-between bg-slate-800"):
        with ui.row().classes("items-center gap-3"):
            ui.label("Дискреция творца").classes("text-lg font-bold")
            ui.label("прогнозирование инцидентов в коллекторах").classes("text-sm opacity-70")
        with ui.row().classes("items-center gap-3"):
            ui.select(
                ["техник", "диспетчер_района", "диспетчер_одс", "группа_реагирования"],
                value=STATE["role"], label="роль",
            ).props("dark dense outlined").classes("w-52").bind_value(STATE, "role")


def verdict_card(v):
    """Карточка вердикта: что, почему, что это НЕ, чего мы не видим."""
    card = v["card"]
    new = v["status"] == "новый"
    border = "border-l-4 border-red-500" if new else "border-l-4 border-slate-300"

    with ui.card().classes(f"w-full {border}"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label(card["location"]).classes("text-base font-semibold")
                ui.label(f"{card.get('sensor_name') or ''} · {card.get('sensor_type') or ''}") \
                    .classes("text-xs opacity-60")
            with ui.row().classes("items-center gap-2"):
                ui.badge(v["scenario"], color=SCENARIO_COLORS.get(v["scenario"], "grey"))
                ui.label(f"{v['probability']:.0%}").classes(
                    f"text-xl font-bold text-{risk_color(v['probability'])}-600")
                ui.label(f"за {v['horizon_hours']} ч").classes("text-xs opacity-60")

        with ui.expansion("Почему так решено", icon="psychology").classes("w-full"):
            for i, e in enumerate(card["evidence"], 1):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(f"{i}. {e['phrase']}").classes("text-sm")
                    ui.label(f"{e['contribution']:+.1%}").classes(
                        "text-sm font-mono opacity-70")
            ui.separator()
            ui.label(
                f"Базовая вероятность для такого канала {card['baseline']:.1%}; "
                "сумма вкладов выше в точности даёт итоговую оценку."
            ).classes("text-xs opacity-60")

        if card.get("not_this"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("block").classes("text-slate-400")
                ui.label("Это НЕ " + "; ".join(card["not_this"])).classes("text-sm opacity-80")

        if card.get("blind_spots"):
            with ui.card().classes("bg-amber-50 w-full p-2"):
                ui.label("Чего мы не видим").classes("text-xs font-semibold opacity-70")
                for b in card["blind_spots"]:
                    ui.label(f"• {b}").classes("text-sm")

        if card.get("counterfactual"):
            ui.label(card["counterfactual"]).classes("text-sm italic opacity-80")

        with ui.row().classes("w-full justify-end gap-2"):
            if new:
                ui.button("Квитировать", icon="check",
                          on_click=lambda vid=v["id"]: do_ack(vid)).props("outline dense")
            ui.button("Решение", icon="assignment",
                      on_click=lambda vid=v["id"]: decision_dialog(vid)).props("dense")


def do_ack(verdict_id):
    con = get_con()
    if con is None:
        ui.notify("Оперативный контур недоступен — режим просмотра", type="warning")
        return
    db.acknowledge(con, verdict_id, STATE["user_id"])
    ui.notify(f"Вердикт {verdict_id} взят в работу")
    feed.refresh()


def decision_dialog(verdict_id):
    """Фиксация решения: то, из чего со временем вырастет разметка."""
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Решение диспетчера").classes("text-lg font-semibold")
        action = ui.select(["выезд", "мониторинг", "без выезда"],
                           value="мониторинг", label="действие").classes("w-full")
        reason = ui.select(
            {"ложная_сработка": "Ложное срабатывание",
             "профилактика": "Профилактика",
             "проверка": "Плановая проверка",
             "испытания": "Технологические испытания",
             "подтверждено": "Неисправность подтверждена",
             "выезд": "Выезд бригады",
             "в_работе": "Передано в ремонт"},
            value="подтверждено", label="причина").classes("w-full")
        comment = ui.textarea(label="комментарий").classes("w-full")

        def save():
            con = get_con()
            if con is None:
                ui.notify("Оперативный контур недоступен", type="warning")
                dialog.close()
                return
            db.record_decision(con, verdict_id, STATE["user_id"],
                               action.value, reason.value, comment.value)
            ui.notify("Решение зафиксировано")
            dialog.close()
            feed.refresh()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Отмена", on_click=dialog.close).props("flat")
            ui.button("Сохранить", on_click=save)
    dialog.open()


@ui.refreshable
def feed():
    rows, live = load_verdicts()
    if not rows:
        ui.label("Вердиктов нет").classes("opacity-60")
        return

    total = len(rows)
    high = sum(1 for r in rows if r["probability"] >= 0.8)
    new = sum(1 for r in rows if r["status"] == "новый")
    blind = sum(1 for r in rows if r["card"].get("blind_spots"))

    with ui.row().classes("w-full gap-4"):
        for title, value, color in (
            ("В очереди", total, "slate"),
            ("Высокий риск", high, "red"),
            ("Не отработано", new, "orange"),
            ("С ограниченной наблюдаемостью", blind, "amber"),
        ):
            with ui.card().classes("flex-1"):
                ui.label(str(value)).classes(f"text-3xl font-bold text-{color}-600")
                ui.label(title).classes("text-xs opacity-70")

    if not live:
        ui.label("Источник: seed-файл (PostgreSQL недоступен)") \
            .classes("text-xs opacity-50")

    for v in rows[:50]:
        verdict_card(v)


@ui.page("/")
def index():
    header()
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):
        ui.label("Очередь на проверку").classes("text-xl font-semibold")
        ui.label(
            "Отсортировано по вероятности отказа в заданном горизонте. "
            "Каждый вердикт раскрывается в обоснование с вкладом каждой улики."
        ).classes("text-sm opacity-70")
        feed()


# ------------------------------------------------------------- REST API

@app.get("/api/verdicts")
def api_verdicts(scenario: str = None, status: str = None, limit: int = 100):
    """Вердикты для внешних систем — требование ТЗ о REST-интерфейсе."""
    con = get_con()
    if con is None:
        rows, _ = load_verdicts(limit)
        return {"count": len(rows), "items": rows}
    rows = db.fetch_verdicts(con, status=status, scenario=scenario, limit=limit)
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    return {"count": len(rows), "items": rows}


@app.get("/api/health")
def api_health():
    con = get_con()
    return {"status": "ok", "database": con is not None}


def main():
    ui.run(host="0.0.0.0", port=8080, title="Дискреция творца", reload=False,
           favicon="🛠", dark=False, show=False)


main()
