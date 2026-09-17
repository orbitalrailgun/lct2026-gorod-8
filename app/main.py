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

from core import cards, config, db, explain

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
            ui.button("Очередь", on_click=lambda: ui.navigate.to("/")).props("flat dense color=white")
            ui.button("Схема", on_click=lambda: ui.navigate.to("/scheme")).props("flat dense color=white")
            ui.button("Журнал", on_click=lambda: ui.navigate.to("/journal")).props("flat dense color=white")
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
                ui.label(card.get("title") or card["location"]).classes("text-base font-semibold")
                sub = f"{card.get('sensor_name') or ''} · {card.get('sensor_type') or ''}"
                inc = cards.incubation_phrase(card.get("incubation_days"))
                ui.label(sub).classes("text-xs opacity-60")
                if inc:
                    ui.label(inc).classes("text-xs opacity-70 italic")
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

        if card.get("timeline"):
            with ui.expansion("Хронология", icon="schedule").classes("w-full"):
                for e in card["timeline"]:
                    with ui.row().classes("w-full items-baseline gap-3"):
                        ui.label(e["ts"][:10]).classes("text-xs font-mono opacity-60 w-24")
                        ui.badge(e["kind"]).props("outline")
                        ui.label(e["text"]).classes("text-sm")

        if card.get("recommendation"):
            with ui.card().classes("bg-emerald-50 w-full p-2"):
                with ui.row().classes("items-start gap-2"):
                    ui.icon("build").classes("text-emerald-700 mt-1")
                    with ui.column().classes("gap-0"):
                        ui.label("Что делать").classes("text-xs font-semibold opacity-70")
                        ui.label(card["recommendation"]).classes("text-sm")
                        if card.get("precedent"):
                            ui.label(card["precedent"]).classes("text-xs opacity-70")

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
    rows, live = load_verdicts(400)
    if not rows:
        ui.label("Вердиктов нет").classes("opacity-60")
        return

    multicards = cards.group_by_object(rows)

    total = len(rows)
    high = sum(1 for r in rows if r["probability"] >= 0.8)
    new = sum(1 for r in rows if r["status"] == "новый")
    blind = sum(1 for r in rows if r["card"].get("blind_spots"))

    with ui.row().classes("w-full gap-4"):
        for title, value, color in (
            ("Объектов в очереди", len(multicards), "slate"),
            ("Вердиктов", total, "slate"),
            ("Высокий риск", high, "red"),
            ("Не отработано", new, "orange"),
            ("Ограниченная наблюдаемость", blind, "amber"),
        ):
            with ui.card().classes("flex-1"):
                ui.label(str(value)).classes(f"text-2xl font-bold text-{color}-600")
                ui.label(title).classes("text-xs opacity-70")

    if not live:
        ui.label("Источник: seed-файл (PostgreSQL недоступен)").classes("text-xs opacity-50")

    for m in multicards:
        multicard(m)


def multicard(m):
    """Мультикарточка объекта: одна поездка бригады — одна сущность."""
    risk = m["max_probability"]
    with ui.card().classes(f"w-full border-l-4 border-{risk_color(risk)}-500"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0 flex-1"):
                ui.label(m["object_name"]).classes("text-lg font-semibold")
                ui.label(m["summary"]).classes("text-sm opacity-75")
            with ui.column().classes("items-end gap-0"):
                ui.label(f"{risk:.0%}").classes(
                    f"text-2xl font-bold text-{risk_color(risk)}-600")
                ui.label(f"{m['n_cards']} вердиктов").classes("text-xs opacity-60")

        with ui.row().classes("gap-2 items-center"):
            for scenario, count in m["scenarios"].items():
                ui.badge(f"{scenario}: {count}",
                         color=SCENARIO_COLORS.get(scenario, "grey"))
            if m.get("first_sign_at"):
                ui.label(f"первые признаки {m['first_sign_at'][:10]}").classes(
                    "text-xs opacity-60")

        note = cards.blind_spot_summary(m["cards"])
        if note:
            with ui.card().classes("bg-amber-50 w-full p-2"):
                ui.label(note).classes("text-sm")

        with ui.expansion(f"Общая хронология объекта ({len(m['timeline'])} записей)",
                          icon="timeline").classes("w-full"):
            for e in m["timeline"]:
                with ui.row().classes("w-full items-baseline gap-3"):
                    ui.label(e["ts"][:10]).classes("text-xs font-mono opacity-60 w-24")
                    ui.badge(e["kind"]).props("outline")
                    ui.label(e["text"]).classes("text-sm")

        with ui.expansion(f"Вердикты объекта ({m['n_cards']})",
                          icon="list").classes("w-full"):
            for v in m["cards"]:
                verdict_card(v)


@ui.page("/")
def index():
    header()
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Очередь на проверку").classes("text-xl font-semibold")
            ui.select({None: "все сценарии", "отказ": "отказы",
                       "подтопление": "подтопление", "пожар": "задымление",
                       "проникновение": "проникновение"},
                      value=None, label="сценарий",
                      on_change=lambda e: (STATE.update(scenario=e.value), feed.refresh())
                      ).props("dense outlined").classes("w-52")
        ui.label(
            "Отсортировано по вероятности отказа в заданном горизонте. "
            "Каждый вердикт раскрывается в обоснование с вкладом каждой улики."
        ).classes("text-sm opacity-70")
        feed()


# ------------------------------------------------- линейная схема

def build_scheme(rows):
    """Раскладка вердиктов по объектам и пикетам.

    Географических координат в данных нет, и заказчик прямо разрешил их
    не восстанавливать: «можно просто сгенерировать любые линейные объекты,
    пикет у нас это 10 метров». Коллектор физически вьётся, но для работы
    диспетчера важна не форма трассы, а положение вдоль неё — поэтому
    рисуется развёртка в линию, как он и предложил.
    """
    by_object = {}
    for r in rows:
        if r["picket"] is None or not r["object_name"]:
            continue
        by_object.setdefault(r["object_name"], []).append(r)
    for name in by_object:
        by_object[name].sort(key=lambda x: x["picket"])
    return by_object


@ui.page("/scheme")
def scheme_page():
    header()
    rows, _ = load_verdicts(300)
    by_object = build_scheme(rows)

    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):
        ui.label("Линейная схема коллекторов").classes("text-xl font-semibold")
        ui.label(
            "Развёртка трассы по пикетам: один пикет — 10 метров. "
            "Отмечены точки, по которым сформированы вердикты."
        ).classes("text-sm opacity-70")

        if not by_object:
            ui.label("Нет вердиктов с привязкой к пикету").classes("opacity-60")
            return

        for object_name, items in sorted(by_object.items(),
                                         key=lambda kv: -len(kv[1])):
            pickets = [r["picket"] for r in items]
            lo, hi = min(pickets), max(pickets)
            span = max(hi - lo, 1)

            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-baseline justify-between"):
                    ui.label(object_name).classes("font-semibold")
                    ui.label(f"ПК{lo}–ПК{hi} · {(hi - lo) * 10} м · "
                             f"{len(items)} вердиктов").classes("text-xs opacity-60")

                marks = []
                for r in items:
                    x = 40 + (r["picket"] - lo) / span * 820
                    color = {"отказ": "#f59e0b", "подтопление": "#3b82f6",
                             "пожар": "#ef4444"}.get(r["scenario"], "#94a3b8")
                    radius = 5 + 5 * r["probability"]
                    marks.append(
                        f'<circle cx="{x:.0f}" cy="30" r="{radius:.1f}" fill="{color}" '
                        f'opacity="0.85"><title>ПК{r["picket"]} · {r["scenario"]} · '
                        f'{r["probability"]:.0%}</title></circle>')

                ui.html(
                    '<svg viewBox="0 0 900 60" style="width:100%;height:60px">'
                    '<line x1="40" y1="30" x2="860" y2="30" stroke="#cbd5e1" stroke-width="3"/>'
                    f'<text x="10" y="34" font-size="11" fill="#64748b">ПК{lo}</text>'
                    f'<text x="866" y="34" font-size="11" fill="#64748b">ПК{hi}</text>'
                    + "".join(marks) + "</svg>")

        with ui.row().classes("gap-4 text-xs opacity-70"):
            for label, color in (("отказ датчика", "#f59e0b"),
                                 ("подтопление", "#3b82f6"),
                                 ("задымление", "#ef4444")):
                with ui.row().classes("items-center gap-1"):
                    ui.html(f'<span style="display:inline-block;width:10px;height:10px;'
                            f'border-radius:50%;background:{color}"></span>')
                    ui.label(label)
        ui.label("Размер точки пропорционален вероятности.").classes("text-xs opacity-60")


@ui.page("/journal")
def journal_page():
    """Журнал прогнозов: что предсказали и чем это кончилось.

    Требование ТЗ — реестр событий с историей прогнозов и результатами их
    отработки. Он же со временем становится источником разметки: пары
    «вердикт — решение диспетчера» это ровно то, чего сейчас нет ни у нас,
    ни у заказчика.
    """
    header()
    con = get_con()
    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        ui.label("Журнал прогнозов").classes("text-xl font-semibold")
        ui.label(
            "История вердиктов и принятых по ним решений. Накопленные пары "
            "«прогноз — исход» используются для дообучения."
        ).classes("text-sm opacity-70")

        if con is None:
            ui.label("Оперативный контур недоступен").classes("opacity-60")
            return

        with con.cursor() as cur:
            cur.execute("""
                SELECT v.id, v.created_at, v.scenario, v.object_name, v.picket,
                       v.probability, v.status,
                       d.action, r.title AS reason, u.full_name, d.decided_at
                FROM verdict v
                LEFT JOIN decision d ON d.verdict_id = v.id
                LEFT JOIN reason_ref r ON r.id = d.reason_id
                LEFT JOIN app_user u ON u.id = d.user_id
                ORDER BY v.probability DESC LIMIT 200
            """)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        closed = [r for r in rows if r["status"] == "закрыт"]
        false_alarms = [r for r in closed if r["reason"] in
                        ("Ложное срабатывание", "Профилактика", "Плановая проверка",
                         "Технологические испытания")]
        with ui.row().classes("w-full gap-4"):
            for title, value in (("Всего вердиктов", len(rows)),
                                 ("Отработано", len(closed)),
                                 ("Признано ложными", len(false_alarms))):
                with ui.card().classes("flex-1"):
                    ui.label(str(value)).classes("text-2xl font-bold")
                    ui.label(title).classes("text-xs opacity-70")

        ui.table(
            columns=[
                {"name": "id", "label": "№", "field": "id", "align": "left"},
                {"name": "scenario", "label": "сценарий", "field": "scenario"},
                {"name": "object_name", "label": "объект", "field": "object_name"},
                {"name": "picket", "label": "ПК", "field": "picket"},
                {"name": "prob", "label": "риск", "field": "prob"},
                {"name": "status", "label": "статус", "field": "status"},
                {"name": "action", "label": "решение", "field": "action"},
                {"name": "reason", "label": "причина", "field": "reason"},
            ],
            rows=[{**r, "prob": f"{r['probability']:.0%}"} for r in rows],
            row_key="id",
        ).classes("w-full").props("dense flat")


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
