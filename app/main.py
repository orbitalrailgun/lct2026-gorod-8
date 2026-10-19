"""Веб-интерфейс диспетчера.

Главный экран — не график и не карта, а лента вердиктов. Диспетчер работает
очередью: что проверить в первую очередь и почему. Каждая строка раскрывается
в карточку с обоснованием.

Квитирование сделано буквально так, как описал заказчик: неотработанный вердикт
визуально выделен и остаётся в ленте, пока диспетчер его не закрыл. В системе
ОЭК тот же принцип реализован миганием до квитирования.
"""

import hmac
import json
import os

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from nicegui import app, ui

from core import (auth, cards, charts, config, db, exchange, explain,
                  orders, plural, reports, synthetic)

SEED = os.path.join(config.ROOT, "deploy", "seed_verdicts.json")
GEOMETRY = os.path.join(config.ROOT, "deploy", "seed_geometry.json")

# Цвет сценария задаётся в одном месте — core/charts.py — и одинаков
# на диаграммах, на карте и на линейной схеме. Палитра проверена на
# различимость при дальтонизме, поэтому менять её поштучно нельзя.
SCENARIO_HEX = dict(charts.SCENARIO)
NEUTRAL_HEX = "#8a8a85"

STATE = {"scenario": None, "status": None}


# ------------------------------------------------------------- доступ

def current_user():
    """Пользователь текущей сессии или None."""
    try:
        return app.storage.user.get("user")
    except Exception:
        return None


def require_login():
    """Страницы закрыты: без входа перенаправляем на форму."""
    if current_user() is None:
        ui.navigate.to("/login")
        return False
    return True


def may_decide():
    """Имеет ли текущий пользователь право фиксировать решения.

    Право объявлено в ролевой модели с самого начала, но до 27.09 нигде
    не проверялось: техник видел те же кнопки, что и диспетчер. Ролевая
    модель, которая описана, но не применяется, хуже её отсутствия —
    она создаёт ложную уверенность в разграничении.
    """
    user = current_user()
    return bool(user) and auth.rights(user["role"])["can_decide"]


def deny_decision():
    """Отказ в действии, требующем права решения."""
    ui.notify("Недостаточно прав: роль работает в режиме просмотра",
              type="warning")


def visible_to_user(rows):
    """Фильтр по области видимости роли.

    Техник видит свой объект, диспетчер района — назначенные, диспетчер ОДС —
    всё предприятие. Область задаётся таблицей user_object, а не ролью напрямую,
    чтобы назначения менялись без правки кода.
    """
    user = current_user()
    if not user:
        return []
    if auth.rights(user["role"])["scope"] == "всё":
        return rows
    con = get_con()
    if con is None:
        return rows
    allowed = auth.visible_objects(con, user)
    if not allowed:
        return rows          # назначений нет — показываем всё, но без права решения
    return [r for r in rows if r.get("object_id") in allowed]

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


def fmt_ts(ts):
    """Метка времени: с точностью до секунды там, где момент известен."""
    text = str(ts or "")
    if len(text) > 10:
        return text[:19].replace("T", " ")
    return text


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
            ui.button("Карта", on_click=lambda: ui.navigate.to("/map")).props("flat dense color=white")
            ui.button("Журнал", on_click=lambda: ui.navigate.to("/journal")).props("flat dense color=white")
            ui.button("Заявки", on_click=lambda: ui.navigate.to("/orders")).props("flat dense color=white")
            ui.button("Аналитика", on_click=lambda: ui.navigate.to("/analytics")) \
                .props("flat dense color=white")
            ui.button("Точность", on_click=lambda: ui.navigate.to("/improve")) \
                .props("flat dense color=white")
            user = current_user()
            if user and auth.rights(user["role"])["can_admin"]:
                ui.button("Настройки", on_click=lambda: ui.navigate.to("/admin")) \
                    .props("flat dense color=white")
            ui.button("Отчёт XLSX", on_click=lambda: ui.download("/api/report.xlsx")) \
                .props("flat dense color=white")
            if user:
                with ui.column().classes("gap-0 items-end"):
                    ui.label(user["full_name"]).classes("text-sm")
                    ui.label(auth.rights(user["role"])["title"]).classes("text-xs opacity-60")
                ui.button(icon="logout", on_click=do_logout).props("flat dense color=white")


def do_logout():
    user = current_user()
    con = get_con()
    if con is not None and user:
        db.log_action(con, user["id"], "выход из системы")
    app.storage.user.clear()
    ui.navigate.to("/login")


def lazy_content(expansion, build):
    """Наполнение раскрывающегося блока по первому раскрытию.

    В очереди тринадцать мультикарточек, внутри них полторы сотни вердиктов
    с хронологиями. Отрисовать это всё заранее — значит собирать на сервере
    тысячи элементов на каждый запрос страницы: замер показал 11 секунд
    при двадцати одновременных пользователях против 0,5 секунды в одиночку.
    Диспетчер при этом раскрывает единицы, а видит сводку.
    """
    # Место под содержимое создаётся внутри самого блока. Если создать его
    # снаружи — а так и было, пока тесты не показали пустой раскрытый блок, —
    # содержимое отрисуется рядом с блоком и останется видимым после
    # его закрытия.
    with expansion:
        slot = ui.column().classes("w-full gap-0")

    def draw(event):
        if not event.value or slot.default_slot.children:
            return
        with slot:
            build()

    expansion.on_value_change(draw)


def evidence_chart(expansion, evidence):
    """Диаграмма вкладов, которая рисуется при раскрытии карточки.

    Диаграмма показывает ровно то же, что список улик рядом: длина столбика —
    вклад улики в решение. Числа продублированы подписями, поэтому график
    читается и без цвета.

    Строится лениво и один раз. В очереди бывает под две сотни карточек,
    и создавать столько же графиков заранее — значит подвесить браузер
    ради того, чего никто не смотрит: диспетчер раскрывает единицы.
    """
    slot = ui.column().classes("w-full gap-0")

    def draw(event):
        if not event.value or slot.default_slot.children:
            return
        with slot:
            ui.echart(charts.evidence([
                {"short": charts.shorten(e["phrase"]),
                 "contribution": e["contribution"]}
                for e in evidence
            ])).classes("w-full").style(f"height: {60 + 26 * len(evidence)}px")

    expansion.on_value_change(draw)


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

        with ui.expansion("Почему так решено", icon="psychology").classes("w-full") as why:
            for i, e in enumerate(card["evidence"], 1):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(f"{i}. {e['phrase']}").classes("text-sm")
                    ui.label(f"{e['contribution']:+.1%}").classes(
                        "text-sm font-mono opacity-70")
            if len(card["evidence"]) > 1:
                evidence_chart(why, card["evidence"])
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
                        ui.label(fmt_ts(e["ts"])).classes("text-xs font-mono opacity-60 w-36")
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

        with ui.row().classes("w-full justify-end gap-2 items-center"):
            if v.get("synthetic"):
                # По синтетическому вердикту нельзя ни квитировать, ни
                # зафиксировать решение: решения диспетчеров — это будущая
                # настоящая разметка, и выдуманным событиям там не место.
                ui.label("экспериментальный объект: решения не фиксируются") \
                    .classes("text-xs opacity-60")
            elif not may_decide():
                ui.label("режим просмотра: фиксация решений недоступна для роли") \
                    .classes("text-xs opacity-60")
            else:
                if new:
                    ui.button("Квитировать", icon="check",
                              on_click=lambda vid=v["id"]: do_ack(vid)).props("outline dense")
                ui.button("Решение", icon="assignment",
                          on_click=lambda vid=v["id"]: decision_dialog(vid)).props("dense")
                ui.button("Заявка", icon="construction",
                          on_click=lambda vv=v: work_order_dialog(vv)).props("outline dense")


def do_ack(verdict_id):
    if not may_decide():
        deny_decision()
        return
    con = get_con()
    if con is None:
        ui.notify("Оперативный контур недоступен — режим просмотра", type="warning")
        return
    db.acknowledge(con, verdict_id, (current_user() or {}).get("id"))
    ui.notify(f"Вердикт {verdict_id} взят в работу")
    feed.refresh()


def decision_dialog(verdict_id):
    """Фиксация решения: то, из чего со временем вырастет разметка."""
    if not may_decide():
        deny_decision()
        return
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
            db.record_decision(con, verdict_id, (current_user() or {}).get("id"),
                               action.value, reason.value, comment.value)
            ui.notify("Решение зафиксировано")
            dialog.close()
            feed.refresh()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Отмена", on_click=dialog.close).props("flat")
            ui.button("Сохранить", on_click=save)
    dialog.open()


def work_order_dialog(verdict):
    """Черновик заявки: поля выведены из вердикта, диспетчер их проверяет.

    Обоснование не редактируется как свободный текст — оно перенесено
    из карточки целиком. Если бы его набирали заново, заявка со временем
    начала бы расходиться с данными, на которых построен прогноз.
    """
    if not may_decide():
        deny_decision()
        return
    con = get_con()
    day = config.__dict__.get("DEMO_DAY", "2026-05-06")
    draft = orders.build_draft(verdict, day,
                               author=(current_user() or {}).get("full_name"))

    existing = db.work_order_exists(con, verdict["id"]) if con is not None else None

    with ui.dialog() as dialog, ui.card().classes("w-[40rem]"):
        ui.label("Черновик заявки на ремонт").classes("text-lg font-semibold")
        ui.label(draft["title"] or "").classes("text-sm opacity-70")
        if existing:
            ui.label(f"По этому вердикту уже есть заявка № {existing}") \
                .classes("text-sm text-amber-700")
        ui.separator()

        wt = ui.input("тип работ", value=draft["work_type"]).classes("w-full")
        with ui.row().classes("w-full gap-2"):
            pr = ui.select(list(orders.PRIORITIES), value=draft["priority"],
                           label="приоритет").classes("flex-1")
            due = ui.input("срок", value=str(draft["due_date"])).classes("flex-1")
        resp = ui.input("исполнитель", value=draft["responsible"]).classes("w-full")

        with ui.expansion("Обоснование (из карточки вердикта)",
                          icon="fact_check").classes("w-full"):
            ui.label(draft["justification"]).classes("text-sm whitespace-pre-wrap")
        if draft.get("recommendation"):
            ui.label("Что делать: " + draft["recommendation"]).classes("text-sm opacity-80")

        def save():
            c = get_con()
            if c is None:
                ui.notify("Оперативный контур недоступен", type="warning")
                dialog.close()
                return
            draft.update(work_type=wt.value, priority=pr.value,
                         due_date=due.value, responsible=resp.value)
            order_id = db.create_work_order(c, draft, (current_user() or {}).get("id"))
            ui.notify(f"Черновик заявки № {order_id} создан")
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Отмена", on_click=dialog.close).props("flat")
            ui.button("Создать черновик", on_click=save)
    dialog.open()


@ui.page("/orders")
def orders_page():
    """Реестр черновиков заявок.

    Реальная отправка во внешнюю систему не требуется — журнал ОДС ведётся
    отдельно и интегрирован не будет. Заявки формируются здесь и выгружаются.
    """
    if not require_login():
        return
    header()
    con = get_con()
    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-baseline justify-between"):
            ui.label("Черновики заявок").classes("text-xl font-semibold")
            ui.button("Выгрузить XLSX",
                      on_click=lambda: ui.download("/api/report.xlsx")).props("flat dense")
        ui.label(
            "Заявка формируется из вердикта: тип работ, приоритет и срок выводятся "
            "из вероятности и вызревания признаков, обоснование переносится "
            "из карточки целиком."
        ).classes("text-sm opacity-70")

        if con is None:
            ui.label("Оперативный контур недоступен").classes("opacity-60")
            return

        rows = db.fetch_work_orders(con)
        if not rows:
            ui.label("Черновиков пока нет — создайте заявку из карточки вердикта") \
                .classes("opacity-60")
            return

        by_priority = {}
        for r in rows:
            by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
        with ui.row().classes("w-full gap-4"):
            for pr in reversed(orders.PRIORITIES):
                with ui.card().classes("flex-1"):
                    ui.label(str(by_priority.get(pr, 0))).classes("text-2xl font-bold")
                    ui.label(pr).classes("text-xs opacity-70")

        for r in rows:
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"№ {r['id']} · {r['work_type']}").classes("font-semibold")
                        where = r["object_name"] or ""
                        if r["picket"] is not None:
                            where += f", ПК{r['picket']}"
                        ui.label(f"{where} · {r['responsible']}").classes("text-xs opacity-60")
                    with ui.column().classes("items-end gap-0"):
                        ui.badge(r["priority"])
                        ui.label(f"до {r['due_date']:%d.%m.%Y}" if r["due_date"] else "") \
                            .classes("text-xs opacity-60")
                with ui.expansion("Обоснование", icon="fact_check").classes("w-full"):
                    ui.label(r["justification"] or "").classes("text-sm whitespace-pre-wrap")


EXPERIMENT_VERDICTS = os.path.join(config.ROOT, "deploy",
                                   "seed_experiment_verdicts.json")


def load_experiment_verdicts():
    """Вердикты синтетического объекта.

    Держатся отдельным файлом и в базу не загружаются: синтетика не должна
    попасть ни в счётчики, ни в отчёты, ни в журнал прогнозов. В ленте они
    показываются последними и с явной меткой.
    """
    if not os.path.exists(EXPERIMENT_VERDICTS):
        return []
    with open(EXPERIMENT_VERDICTS, encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = []
    for index, verdict in enumerate(payload.get("вердикты", []), 1):
        rows.append({
            "id": -index,                     # отрицательные: не из базы
            "created_at": None, "status": "синтетический",
            "scenario": verdict["scenario"],
            "object_id": verdict.get("object_id"),
            "object_name": verdict.get("object_name"),
            "picket": verdict.get("picket"),
            "channel_id": verdict.get("channel_id"),
            "probability": verdict["probability"],
            "horizon_hours": verdict.get("horizon_hours", config.HORIZON_HOURS),
            "model_version": verdict.get("model_version", ""),
            "card": verdict["card"], "synthetic": True,
        })
    return rows


@ui.refreshable
def feed():
    rows, live = load_verdicts(400)
    rows = visible_to_user(rows)
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

    # Экспериментальный объект идёт последним и отделён от реальной сети.
    # Он не участвует в счётчиках выше: это синтетика, и смешивать её
    # с рабочей очередью нельзя даже визуально.
    experiment = load_experiment_verdicts()
    if experiment:
        ui.separator()
        with ui.row().classes("w-full items-center gap-2"):
            ui.badge("синтетический объект", color="purple")
            ui.label(
                plural.count(len(experiment), "вердикт", "вердикта",
                             "вердиктов")
                + " эксперимента по дооснащению: в счётчики, отчёты и "
                  "выгрузки не входят, решения по ним не фиксируются"
            ).classes("text-sm opacity-70")
            ui.button("Подробнее", on_click=lambda: ui.navigate.to("/improve")) \
                .props("flat dense")
        for m in cards.group_by_object(experiment):
            multicard(m)


def multicard(m):
    """Мультикарточка объекта: одна поездка бригады — одна сущность."""
    risk = m["max_probability"]
    synthetic_object = any(v.get("synthetic") for v in m["cards"])
    border = "purple" if synthetic_object else risk_color(risk)
    with ui.card().classes(f"w-full border-l-4 border-{border}-500"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0 flex-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(m["object_name"]).classes("text-lg font-semibold")
                    if synthetic_object:
                        ui.badge("синтетика", color="purple")
                ui.label(m["summary"]).classes("text-sm opacity-75")
            with ui.column().classes("items-end gap-0"):
                ui.label(f"{risk:.0%}").classes(
                    f"text-2xl font-bold text-{risk_color(risk)}-600")
                ui.label(plural.count(m["n_cards"], "вердикт", "вердикта", "вердиктов")) \
                    .classes("text-xs opacity-60")

        with ui.row().classes("gap-2 items-center"):
            for scenario, count in m["scenarios"].items():
                ui.badge(f"{scenario}: {count}",
                         color=SCENARIO_COLORS.get(scenario, "grey"))
            if m.get("first_sign_at"):
                ui.label(f"первые признаки {m['first_sign_at'][:10]}").classes(
                    "text-xs opacity-60")

        for casc in (m.get("cascades") or [])[:2]:
            with ui.card().classes("bg-rose-50 w-full p-2"):
                with ui.row().classes("items-start gap-2"):
                    ui.icon("bolt").classes("text-rose-700 mt-1")
                    with ui.column().classes("gap-0"):
                        ui.label("Каскад").classes("text-xs font-semibold opacity-70")
                        ui.label(casc["text"]).classes("text-sm")

        note = cards.blind_spot_summary(m["cards"])
        if note:
            with ui.card().classes("bg-amber-50 w-full p-2"):
                ui.label(note).classes("text-sm")

        timeline = ui.expansion(
            "Общая хронология объекта ("
            + plural.count(len(m["timeline"]), "запись", "записи", "записей") + ")",
            icon="timeline").classes("w-full")

        def draw_timeline(marks=m["timeline"]):
            for e in marks:
                with ui.row().classes("w-full items-baseline gap-3"):
                    ui.label(fmt_ts(e["ts"])).classes(
                        "text-xs font-mono opacity-60 w-36")
                    ui.badge(e["kind"]).props("outline")
                    ui.label(e["text"]).classes("text-sm")

        lazy_content(timeline, draw_timeline)

        listing = ui.expansion(f"Вердикты объекта ({m['n_cards']})",
                               icon="list").classes("w-full")

        def draw_cards(items=m["cards"]):
            for v in items:
                verdict_card(v)

        lazy_content(listing, draw_cards)


@ui.page("/login")
def login_page():
    """Вход с двухфакторной аутентификацией."""
    con = get_con()
    users = auth.list_users(con) if con is not None else []

    with ui.column().classes("w-full max-w-md mx-auto mt-24 gap-4"):
        with ui.card().classes("w-full"):
            ui.label("Дискреция творца").classes("text-xl font-bold")
            ui.label("Сервис прогнозирования инцидентов в коллекторах") \
                .classes("text-sm opacity-70")
            ui.separator()

            login_field = ui.select(
                {u["login"]: f"{u['full_name']} — {auth.rights(u['role'])['title']}"
                 for u in users},
                label="пользователь",
                value=users[0]["login"] if users else None).classes("w-full")
            code_field = ui.input("одноразовый код").classes("w-full")
            message = ui.label("").classes("text-sm text-red-600")

            def do_login():
                c = get_con()
                if c is None:
                    message.text = "оперативный контур недоступен"
                    return
                user, err = auth.login(c, login_field.value, code_field.value)
                if err:
                    message.text = err
                    return
                app.storage.user["user"] = user
                ui.navigate.to("/")

            ui.button("Войти", on_click=do_login).classes("w-full")

            # Подсказка с текущим кодом нужна только для демонстрации:
            # в эксплуатации второй фактор приходит из приложения-аутентификатора.
            if users and con is not None:
                with ui.expansion("Код для демонстрации", icon="help").classes("w-full"):
                    def show_code():
                        u = auth.get_user(get_con(), login_field.value)
                        hint.text = f"текущий код: {auth.current_code(u['totp_secret'])}"
                    hint = ui.label("").classes("text-sm font-mono")
                    ui.button("Показать", on_click=show_code).props("flat dense")


@ui.page("/admin")
def admin_page():
    """Настраиваемые параметры.

    Организаторы ожидают, что пороги качества и горизонт прогноза задаются
    администратором, а не зашиты в код: «у администратора есть возможность
    поправить». Это же закрывает пункт ТЗ о дополнительных настраиваемых
    параметрах.
    """
    if not require_login():
        return
    user = current_user()
    if not auth.rights(user["role"])["can_admin"]:
        header()
        ui.label("Недостаточно прав").classes("m-8 opacity-70")
        return

    header()
    con = get_con()
    with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
        ui.label("Настройки сервиса").classes("text-xl font-semibold")
        ui.label(
            "Пороги качества и горизонт прогноза задаются здесь, а не в коде. "
            "Целевые значения ТЗ — Precision 0,70 и Recall 0,50; достижимость "
            "зависит от горизонта и обоснована в пояснительной записке."
        ).classes("text-sm opacity-70")

        if con is None:
            ui.label("Оперативный контур недоступен").classes("opacity-60")
            return

        with con.cursor() as cur:
            cur.execute("SELECT key, value, title FROM setting ORDER BY key")
            settings = cur.fetchall()

        fields = {}
        for key, value, title in settings:
            fields[key] = ui.input(title, value=value).classes("w-full")

        def save():
            c = get_con()
            for key, field in fields.items():
                db.set_setting(c, key, field.value, user["id"])
            ui.notify("Настройки сохранены")

        ui.button("Сохранить", on_click=save)

        ui.separator()
        ui.label("Журнал действий").classes("text-lg font-semibold")
        with con.cursor() as cur:
            cur.execute("""
                SELECT a.at, u.full_name, a.action, a.entity, a.entity_id
                FROM audit_log a LEFT JOIN app_user u ON u.id = a.user_id
                ORDER BY a.at DESC LIMIT 50
            """)
            logs = cur.fetchall()
        ui.table(
            columns=[{"name": "at", "label": "время", "field": "at"},
                     {"name": "who", "label": "пользователь", "field": "who"},
                     {"name": "what", "label": "действие", "field": "what"},
                     {"name": "obj", "label": "объект", "field": "obj"}],
            rows=[{"at": r[0].strftime("%d.%m %H:%M:%S"), "who": r[1] or "—",
                   "what": r[2], "obj": f"{r[3] or ''} {r[4] or ''}".strip()}
                  for r in logs],
        ).classes("w-full").props("dense flat")


@ui.page("/")
def index():
    if not require_login():
        return
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
    if not require_login():
        return
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
                             + plural.count(len(items), "вердикт", "вердикта",
                                            "вердиктов")).classes("text-xs opacity-60")

                marks = []
                for r in items:
                    x = 40 + (r["picket"] - lo) / span * 820
                    color = SCENARIO_HEX.get(r["scenario"], NEUTRAL_HEX)
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
            for label, color in (("отказ датчика", SCENARIO_HEX["отказ"]),
                                 ("подтопление", SCENARIO_HEX["подтопление"]),
                                 ("задымление", SCENARIO_HEX["пожар"]),
                                 ("проникновение", SCENARIO_HEX["проникновение"])):
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
    if not require_login():
        return
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


def load_geometry():
    if not os.path.exists(GEOMETRY):
        return []
    with open(GEOMETRY, encoding="utf-8") as fh:
        return json.load(fh)


@ui.page("/map")
def map_page():
    """Интерактивная карта с выделением проблемных зон — требование ТЗ.

    Геометрия схематическая и это принципиально проговаривается. Реальных
    координат в данных нет: предприятие использует внутреннюю систему координат,
    пересчёт которой заказчик вынес за рамки проекта, разрешив сгенерировать
    линейные объекты самостоятельно. Мы привязали трассы к районам по топонимам,
    уцелевшим в названиях датчиков, поэтому коллекторы показаны примерно там,
    где они и проходят, — но это ориентир, а не результат съёмки.
    """
    if not require_login():
        return
    header()

    geometry = load_geometry()
    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-3"):
        with ui.row().classes("w-full items-baseline justify-between"):
            ui.label("Карта объектов").classes("text-xl font-semibold")
            ui.label(plural.count(len(geometry), "объект", "объекта", "объектов")) \
                .classes("text-sm opacity-60")

        with ui.card().classes("bg-amber-50 w-full p-2"):
            ui.label(
                "Геометрия трасс схематическая. Реальные координаты предприятия "
                "во внутренней системе и в датасет не передавались; расположение "
                "восстановлено по районам из названий датчиков, длина участка — "
                "по числу пикетов (1 пикет = 10 м). Для ориентирования этого "
                "достаточно, для съёмочных задач — нет."
            ).classes("text-sm")

        if not geometry:
            ui.label("Геометрия не рассчитана").classes("opacity-60")
            return

        m = ui.leaflet(center=(55.66, 37.55), zoom=11).classes("w-full h-96")

        for g in geometry:
            risk = g["max_probability"]
            color = "#dc2626" if risk >= 0.8 else "#f59e0b" if risk >= 0.5 else "#64748b"
            m.generic_layer(name="polyline", args=[
                [list(p) for p in g["line"]],
                {"color": color, "weight": 6, "opacity": 0.75},
            ])
            for p in g["points"]:
                m.generic_layer(name="circleMarker", args=[
                    [p["lat"], p["lon"]],
                    {"radius": 4 + 6 * p["probability"],
                     "color": SCENARIO_HEX.get(p["scenario"], NEUTRAL_HEX),
                     "fillColor": SCENARIO_HEX.get(p["scenario"], NEUTRAL_HEX),
                     "fillOpacity": 0.8, "weight": 1},
                ])

        with ui.row().classes("gap-4 text-xs opacity-75 flex-wrap"):
            for label, color in (("отказ датчика", SCENARIO_HEX["отказ"]),
                                 ("подтопление", SCENARIO_HEX["подтопление"]),
                                 ("задымление", SCENARIO_HEX["пожар"]),
                                 ("проникновение", SCENARIO_HEX["проникновение"])):
                with ui.row().classes("items-center gap-1"):
                    ui.html(f'<span style="display:inline-block;width:10px;height:10px;'
                            f'border-radius:50%;background:{color}"></span>')
                    ui.label(label)
            ui.label("· толщина трассы и цвет — максимальный риск на объекте")

        ui.label("Проблемные зоны").classes("text-lg font-semibold mt-2")
        for g in sorted(geometry, key=lambda x: -x["max_probability"]):
            if g["max_probability"] < 0.5:
                continue
            with ui.card().classes("w-full p-2"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        place = f" · район {g['toponym']}" if g.get("toponym") else \
                                " · район не определён"
                        ui.label(g["object_name"] + place).classes("font-medium")
                        ui.label(
                            f"ПК{g['picket_min']}–ПК{g['picket_max']}, "
                            f"{g['length_m'] / 1000:.1f} км · "
                            + plural.count(g["n_cards"], "вердикт", "вердикта",
                                           "вердиктов")
                        ).classes("text-xs opacity-60")
                    ui.label(f"{g['max_probability']:.0%}").classes(
                        f"text-lg font-bold text-{risk_color(g['max_probability'])}-600")


# ------------------------------------------------------------- аналитика

ANALYTICS = os.path.join(config.ROOT, "deploy", "seed_analytics.json")


def load_analytics():
    """Предрассчитанные агрегаты для диаграмм качества и наблюдаемости.

    Витрины в контейнер не уезжают, поэтому величины, которые считаются
    по всей истории, готовятся офлайн скриптом scripts/09 и лежат рядом
    с вердиктами.
    """
    if not os.path.exists(ANALYTICS):
        return None
    with open(ANALYTICS, encoding="utf-8") as fh:
        return json.load(fh)


def stat_tile(value, title, note=None, color="text-slate-800"):
    """Плашка с числом. Там, где история — это одно число, график не нужен."""
    with ui.card().classes("p-4 flex-1 min-w-48"):
        ui.label(value).classes(f"text-3xl font-bold {color}")
        ui.label(title).classes("text-sm font-medium")
        if note:
            ui.label(note).classes("text-xs opacity-60")


def chart_block(title, note, option, height=280, table=None):
    """Диаграмма с заголовком, пояснением и таблицей значений.

    Таблица здесь не дубль, а требование доступности: у части цветов
    контраст к белому ниже 3:1, и число обязано быть доступно текстом,
    а не только длиной столбика.
    """
    with ui.card().classes("w-full"):
        ui.label(title).classes("text-base font-semibold")
        ui.label(note).classes("text-xs opacity-70")
        ui.echart(option).classes("w-full").style(f"height: {height}px")
        if table:
            columns, rows = table
            ui.table(columns=columns, rows=rows).classes("w-full").props("dense flat")


@ui.page("/analytics")
def analytics_page():
    """Диаграммы: качество прогноза, наблюдаемость, оперативная картина.

    Страница отвечает на пункт финальной экспертизы об объективности
    диаграмм. Поэтому здесь нет ни одной декоративной: каждая отвечает
    на вопрос, который задают на защите, и каждая построена на величине,
    которую можно проверить в данных.
    """
    if not require_login():
        return
    header()
    data = load_analytics()
    rows, _ = load_verdicts(500)
    rows = visible_to_user(rows)

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        ui.label("Аналитика").classes("text-xl font-semibold")
        ui.label(
            "Качество прогноза, границы наблюдаемости и текущая картина "
            "по объектам. Все величины посчитаны на поставленных данных "
            "и воспроизводятся скриптами репозитория."
        ).classes("text-sm opacity-70")

        # --- плашки: там, где история это одно число
        with_blind = sum(1 for r in rows if r["card"].get("blind_spots"))
        facts = (data or {}).get("facts", {})
        with ui.row().classes("w-full gap-3 flex-wrap"):
            stat_tile(str(len(rows)), "вердиктов в работе",
                      "по всем четырём сценариям")
            stat_tile(f"{with_blind * 100 // max(len(rows), 1)} %",
                      "вердиктов с заявленной слепой зоной",
                      "сервис сам говорит, где он слеп", "text-amber-700")
            if facts:
                stat_tile(f"{facts['orphans']:,}".replace(",", " "),
                          "каналов без паспорта",
                          "исключены из обучения и вердиктов", "text-amber-700")
                stat_tile(f"{facts['guards_stuck']} из {facts['guards']}",
                          "охранных каналов залипли",
                          "показание «под охраной» недостоверно", "text-amber-700")

        # --- качество прогноза
        ui.label("Качество прогноза").classes("text-lg font-semibold mt-2")
        if data and data.get("horizon"):
            with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
                with ui.column().classes("flex-1 min-w-96"):
                    chart_block(
                        "Достижимая точность по горизонтам",
                        "Целевая точность ТЗ достигается, но не на суточном "
                        "горизонте: отказ вызревает неделями.",
                        charts.horizon(data["horizon"]),
                        table=(
                            [{"name": "h", "label": "горизонт", "field": "h"},
                             {"name": "p", "label": "точность", "field": "p"},
                             {"name": "n", "label": "отказов в выборке", "field": "n"}],
                            [{"h": r["label"], "p": f"{r['best_precision']:.0%}",
                              "n": f"{r['positives']:,}".replace(",", " ")}
                             for r in data["horizon"]]))
                with ui.column().classes("flex-1 min-w-96"):
                    chart_block(
                        "Цена полноты: проверок в сутки",
                        "Recall 0,5 из ТЗ означает 713 проверок в смену, "
                        "из которых отказом окажутся две.",
                        charts.recall_cost(data["recall"]),
                        table=(
                            [{"name": "r", "label": "полнота", "field": "r"},
                             {"name": "a", "label": "проверок в сутки", "field": "a"},
                             {"name": "p", "label": "точность", "field": "p"}],
                            [{"r": f"{r['recall']:.0%}",
                              "a": f"{r['alerts_per_day']:.0f}",
                              "p": f"{r['precision']:.2%}"}
                             for r in data["recall"]]))

        # --- наблюдаемость
        ui.label("Что сервис видит и чего не видит").classes("text-lg font-semibold mt-2")
        with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
            if data and data.get("observability"):
                obs = data["observability"]
                with ui.column().classes("flex-1 min-w-96"):
                    chart_block(
                        "Объекты без контроля",
                        "Столбик до конца шкалы означал бы, что контроля нет "
                        "нигде в сети. Там, где контроля нет, вердикт обязан "
                        "сказать об этом вместо «риск низкий».",
                        charts.observability(obs["rows"], obs["objects"]),
                        height=240)
            with ui.column().classes("flex-1 min-w-96"):
                chart_block(
                    "Как копятся признаки",
                    "Из этого распределения выводится срок заявки: "
                    "постепенная деградация закрывается плановой, "
                    "внезапное развитие требует выезда.",
                    charts.incubation(charts.incubation_buckets(
                        [r["card"].get("incubation_days") for r in rows])),
                    height=240)

        # --- оперативная картина
        ui.label("Оперативная картина").classes("text-lg font-semibold mt-2")
        scenarios = [s for s in charts.SCENARIO if any(r["scenario"] == s for r in rows)]
        objects = {}
        for r in rows:
            objects[r["object_name"] or "без объекта"] = \
                objects.get(r["object_name"] or "без объекта", 0) + 1
        order = [name for name, _ in sorted(objects.items(), key=lambda kv: kv[1])]
        matrix = {s: {} for s in scenarios}
        for r in rows:
            name = r["object_name"] or "без объекта"
            matrix[r["scenario"]][name] = matrix[r["scenario"]].get(name, 0) + 1
        if order:
            # Таблица под диаграммой — не дубль, а требование доступности:
            # у жёлтого контраст к белому ниже 3:1, и значение обязано быть
            # доступно текстом, а не только длиной сегмента.
            columns = [{"name": "obj", "label": "объект", "field": "obj",
                        "align": "left"}]
            columns += [{"name": s_, "label": s_, "field": s_} for s_ in scenarios]
            columns.append({"name": "all", "label": "всего", "field": "all"})
            table_rows = []
            for name in reversed(order):
                row = {"obj": name, "all": objects[name]}
                for s_ in scenarios:
                    row[s_] = matrix[s_].get(name, 0) or ""
                table_rows.append(row)
            chart_block(
                "Вердикты по объектам",
                "Диспетчер едет на объект, а не на канал: важно не только "
                "сколько вердиктов, но и какого рода.",
                charts.by_object(order, scenarios, matrix),
                height=max(240, 34 * len(order) + 80),
                table=(columns, table_rows))


# ------------------------------------------------- повышение точности

EXPERIMENT = os.path.join(config.ROOT, "deploy", "seed_experiment.json")
CALIBRATION = os.path.join(config.ROOT, "models", "sensor_calibration.json")
CONTROLS = os.path.join(config.ROOT, "models", "experiment_controls.json")

# Подписи групп наблюдаемости для диаграммы: в файле калибровки они лежат
# короткими ключами, а на экране нужно человеческое название.
GROUP_LABELS = {
    "самодиагностика": "без самодиагностики",
    "инфраструктура": "без сигнала контроллера",
    "связь": "без контроля линии",
    "молчание": "без регулярного опроса",
    "окружение": "без соседних датчиков",
}


def load_json_file(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def own_share(pops):
    """Доля выборки, на которой заработана вся точность.

    Через .get, а не напрямую: файл измерений может оказаться старее
    кода, и падение здесь означало бы пустой экран вместо ответа.
    """
    total = (pops.get("всё вместе") or {}).get("канало-суток") or 0
    own = (pops.get("свой_отказ") or {}).get("канало-суток") or 0
    return own / total if total else 0.0


def thresholds_phrase(row):
    """Пороги срабатывания словами.

    У конфигураций, посчитанных до появления этой оси, поля порогов
    в файле нет. Подставляются базовые значения сети — те же, что
    использовал генератор по умолчанию, а не выдуманные.
    """
    values = row.get("пороги_срабатывания") or synthetic.DEFAULT_THRESHOLDS
    parts = []
    for key in ("heat", "flood", "temperature"):
        if key not in values:
            continue
        label = synthetic.THRESHOLD_LABELS.get(key, key)
        unit = synthetic.THRESHOLD_UNITS.get(key, "")
        parts.append(f"{label} — {values[key]:g} {unit}".strip())
    tail = ("" if row.get("пороги_срабатывания")
            else " (базовые значения сети)")
    return "; ".join(parts) + tail


def queue_cell(rows_count, hits):
    """«23 строки, 1 попадание» — согласованно, а не «23 строк, 1 попаданий»."""
    return (plural.count(rows_count, "строка", "строки", "строк") + ", "
            + plural.count(hits, "попадание", "попадания", "попаданий"))


def _improve_what_is_predicted(controls):
    """Что модель предсказывает на самом деле.

    Раздел стоит первым не из скромности. Пока не разделены «новая
    поломка» и «продолжение уже идущей», любая рекомендация
    по дооснащению опирается на цифру, измеряющую не то, что заказчик
    собирается покупать.
    """
    if controls:
        pops = controls["популяции"]
        split = controls["разложение_позитивов"]
        fresh, ongoing = pops["новый_отказ"], pops["продолжение"]

        ui.label("Что модель предсказывает на самом деле") \
            .classes("text-lg font-semibold mt-2")
        with ui.card().classes("w-full border-l-4 border-amber-500"):
            ui.label(
                "Целевая переменная прогноза отказа — «канал сообщит о "
                "неисправности в следующие сутки». Почти половину веса "
                "модели (0,44 из 1) несут признаки самодиагностики — "
                "сообщения прибора о собственной неисправности, и самый "
                "сильный из них «сколько суток прошло с последнего такого "
                "сообщения» (0,25). То есть цель прогноза и его главное "
                "основание — один и тот же поток событий, сдвинутый "
                "по времени. Поэтому отложенная выборка разделена "
                "на две популяции и метрики считаются в каждой отдельно."
            ).classes("text-sm")

            with ui.row().classes("w-full gap-4 flex-wrap mt-2"):
                for title, pop, share, hint, color in (
                    ("Новый отказ", fresh, split["новый_отказ"]["доля"],
                     "прибор не сообщал о неисправности последние 7 суток — "
                     "именно это и надо предсказать", "red"),
                    ("Продолжение", ongoing, split["продолжение"]["доля"],
                     "прибор уже в отказном состоянии — прогноз близок "
                     "к констатации", "slate"),
                ):
                    with ui.card().classes("flex-1 min-w-80"):
                        with ui.row().classes("items-baseline gap-2"):
                            ui.label(title).classes("text-base font-semibold")
                            ui.badge(f"{share:.0%} позитивов", color=color)
                        ui.label(
                            f"{pop.get('precision@50', 0):.0%}"
                        ).classes(f"text-3xl font-bold text-{color}-600")
                        ui.label("точность в топ-50").classes("text-xs opacity-60")
                        ui.label(
                            plural.count(pop["позитивов"], "отказ",
                                         "отказа", "отказов")
                            + f", {pop['канало-суток']:,} канало-суток"
                            .replace(",", " ")
                        ).classes("text-xs opacity-70")
                        ui.label(hint).classes("text-xs opacity-70 italic")

            with ui.card().classes("bg-amber-50 w-full p-3 mt-2"):
                ui.label("Что из этого следует").classes("text-sm font-semibold")
                ui.label(
                    f"Заявленные {pops['всё вместе'].get('precision@50', 0):.0%} "
                    "точности держатся на каналах, которые сообщили о "
                    "неисправности в последние 7 суток: это "
                    f"{ongoing['канало-суток'] / pops['всё вместе']['канало-суток']:.1%} "
                    f"канало-суток, но {split['продолжение']['доля']:.0%} "
                    "позитивов. На отказах, приходящих без предупреждения — "
                    f"а это {split['новый_отказ']['доля']:.0%} всех отказов, — "
                    f"точность {fresh.get('precision@50', 0):.0%}. "
                    "Две цифры посчитаны в разных популяциях и не "
                    "складываются: 0,02 — не часть 0,18, а отдельное "
                    "измерение на другой выборке."
                ).classes("text-sm")
                ui.label(
                    "Вот это и есть настоящая цель дооснащения: не улучшить "
                    "прогноз там, где прибор уже жалуется, а увидеть то, "
                    "о чём сейчас не сообщает никто."
                ).classes("text-sm font-semibold mt-1")

            strata = controls.get("состав_очереди_по_стратам") or {}
            if strata:
                ui.separator()
                ui.label("Точнее: где именно заработаны эти 18 %").classes(
                    "text-sm font-semibold")
                ui.label(
                    "Популяция «продолжение» склеивает две разные вещи. "
                    "Признак модели суммирует отказные события канала "
                    "без разбора, а целевая переменная массовые базо-дни "
                    "исключает: сервис считает их следами работы бригады, "
                    "а не отказом прибора. Поэтому разрез на три части."
                ).classes("text-xs opacity-70")
                names = (("новый_отказ", "Не сообщал о неисправности"),
                         ("свой_отказ", "Сообщал вне массовых суток"),
                         ("только_массовый_день",
                          "Сообщал только в массовые сутки"))
                ui.table(
                    columns=[
                        {"name": "s", "label": "страт", "field": "s",
                         "align": "left"},
                        {"name": "n", "label": "канало-суток", "field": "n"},
                        {"name": "p", "label": "отказов", "field": "p"},
                        {"name": "b", "label": "база", "field": "b"},
                        {"name": "t", "label": "точность топ-50",
                         "field": "t"},
                    ],
                    rows=[{
                        "s": title,
                        "n": f"{pops[key]['канало-суток']:,}".replace(",", " "),
                        "p": f"{pops[key]['позитивов']}",
                        "b": f"{pops[key].get('базовая_частота', 0):.3%}",
                        "t": f"{pops[key].get('precision@50', 0):.0%}",
                    } for key, title in names if key in pops],
                ).classes("w-full").props("dense flat")

                ui.label(
                    "Метрики по стратам отвечают на вопрос «а если бы "
                    "диспетчер смотрел только на такие каналы». Но "
                    "очередь одна, и прямой вопрос эксплуатации другой: "
                    "кто доходит до её головы. Ранжирование здесь общее, "
                    "строки и попадания разнесены по стратам."
                ).classes("text-xs opacity-70")

                head = strata.get("топ-50") or {}
                own = head.get("свой_отказ", {})
                ui.table(
                    columns=[
                        {"name": "k", "label": "голова очереди",
                         "field": "k", "align": "left"},
                        {"name": "a", "label": "не сообщал",
                         "field": "a", "align": "left"},
                        {"name": "b", "label": "вне массовых суток",
                         "field": "b", "align": "left"},
                        {"name": "c", "label": "только массовые",
                         "field": "c", "align": "left"},
                    ],
                    rows=[{
                        "k": key,
                        "a": queue_cell(row["новый_отказ"]["строк"],
                                        row["новый_отказ"]["попаданий"]),
                        "b": queue_cell(row["свой_отказ"]["строк"],
                                        row["свой_отказ"]["попаданий"]),
                        "c": queue_cell(
                            row["только_массовый_день"]["строк"],
                            row["только_массовый_день"]["попаданий"]),
                    } for key, row in strata.items()],
                ).classes("w-full").props("dense flat")

                if own.get("строк"):
                    ui.label(
                        "В голове очереди нет ни одного канала, который "
                        "не сообщал бы о неисправности последние 7 суток. "
                        "Не «их там меньше» — их там нет вовсе. Эти 77 % "
                        "отказов для очереди сейчас невидимы, и именно "
                        "на них направлен вопрос о дооснащении."
                    ).classes("text-sm font-semibold")
                    ui.label(
                        f"Вся точность заработана на "
                        f"{own_share(pops):.2%} "
                        "выборки. На каналах, сообщавших только в массовые "
                        "сутки, точность нулевая на любом k: эти события "
                        "про работу бригады, а не про прибор, "
                        "и предсказывать в них нечего."
                    ).classes("text-sm")


def _improve_measured(calibration):
    """Что измерено на реальной сети: вклад групп наблюдаемости."""
    ui.label("Что измерено на реальной сети").classes("text-lg font-semibold mt-2")
    if calibration:
        ablation = calibration["вклад_наблюдаемости"]
        baseline = ablation["исходно"]["precision@50"]
        groups = [{"подпись": GROUP_LABELS.get(k, k),
                   "precision@50": v["precision@50"],
                   "даёт": v["даёт"], "потеря": v["потеря@50"]}
                  for k, v in ablation["группы"].items()]
        groups.sort(key=lambda g: g["precision@50"])

        with ui.card().classes("w-full"):
            ui.label("Что держит прогноз отказа").classes("text-base font-semibold")
            ui.label(
                "Каждый столбик — точность модели, когда соответствующего "
                "источника нет. Чем ниже столбик, тем важнее источник."
            ).classes("text-xs opacity-70")
            ui.echart(charts.observability_contribution(groups, baseline)) \
                .classes("w-full").style("height: 300px")
            ui.table(
                columns=[{"name": "g", "label": "без чего", "field": "g",
                          "align": "left"},
                         {"name": "p", "label": "точность", "field": "p"},
                         {"name": "d", "label": "что это даёт", "field": "d",
                          "align": "left"}],
                rows=[{"g": g["подпись"], "p": f"{g['precision@50']:.0%}",
                       "d": g["даёт"]} for g in groups],
            ).classes("w-full").props("dense flat")

        with ui.card().classes("bg-amber-50 w-full p-3"):
            ui.label("Неожиданный результат").classes("text-sm font-semibold")
            ui.label(
                "Признаки соседних датчиков прогноз отказа не улучшают. "
                "Без них точность не падает, а растёт — с 18 % до 20 %. "
                "Поломку прибора предсказывает его собственная история, "
                "а соседи нужны для подтверждения инцидента. "
                "Поэтому ответ на вопрос о дооснащении распадается надвое: "
                "для отказов решают характеристики приборов, для инцидентов — "
                "их набор."
            ).classes("text-sm")
            ui.separator()
            # Вывод «соседи не нужны» был бы здесь переобобщением, и это
            # важно сказать самим, а не ждать вопроса эксперта.
            ui.label("Чем этот результат не является").classes(
                "text-sm font-semibold mt-1")
            ui.label(
                "Это измерение нашего набора признаков, а не физики. "
                "Признаки domain_size, domain_dev_share_7d и "
                "domain_silent_share — счётчик и средние по каналам точки, "
                "поэтому добавление любого прибора арифметически сдвигает "
                "их вниз: богатая точка выглядит для модели спокойнее "
                "просто из-за знаменателя. Правильный вывод — «так, как "
                "эти признаки посчитаны сейчас, соседи прогнозу мешают», "
                "и это повод переделать агрегат, а не отказываться от "
                "датчиков. Величину арифметического вклада измеряет "
                "плацебо-конфигурация в эксперименте ниже."
            ).classes("text-sm")
            if calibration.get("оснащённость_точек"):
                tail = [g for g in calibration["оснащённость_точек"]
                        if g["n_types"] >= 7]
                if tail:
                    ui.label(
                        "Размеры групп по оснащённости: "
                        + ", ".join(
                            plural.count(g["n_types"], "тип", "типа",
                                         "типов") + " — "
                            + plural.count(g["points"], "точка",
                                           "точки", "точек")
                            for g in calibration["оснащённость_точек"])
                        + ". Выводы по двум последним группам статистически "
                          "пусты, и мы их не делаем."
                    ).classes("text-xs opacity-70")


def _improve_experiment(rows, physics, truth):
    """Эксперимент на синтетическом объекте: как устроен и что показал."""
    with ui.card().classes("w-full"):
        ui.label("Как устроен эксперимент").classes("text-base font-semibold")
        ui.label(
            "Одна и та же физика коллектора — "
            + plural.count(physics.get("пикетов", 0), "пикет", "пикета",
                           "пикетов") + ", "
            + plural.count(physics.get("суток", 0), "сутки", "суток",
                           "суток") + ", "
            + plural.count(truth.get("failures", 0), "отказ", "отказа",
                           "отказов") + ", "
            + plural.count(truth.get("fires", 0), "задымление",
                           "задымления", "задымлений") + ", "
            + plural.count(truth.get("floods", 0), "подтопление",
                           "подтопления", "подтоплений") + ", "
            + plural.count(truth.get("intrusions", 0), "проникновение",
                           "проникновения", "проникновений")
            + " суммарно по трём прогонам — прогоняется через разные "
              "конфигурации оснащения. "
            "Модель не переобучается: берётся та же, что работает на реальных "
            "данных. Физика порождается один раз и об оснащении не знает, "
            "поэтому внутри эксперимента разница в метриках — следствие "
            "оснащения. Но абсолютный уровень точности задан допущениями "
            "генератора, и переносить его на реальную сеть нельзя: "
            "осмысленны порядок конфигураций и форма кривых."
        ).classes("text-sm opacity-80")
        ui.separator()
        # Без этого абзаца читатель сравнит 3 % эксперимента с 18 %
        # реальной сети и сделает вывод о качестве модели, тогда как
        # это две разные величины.
        ui.label("Почему эти проценты нельзя сравнивать с реальными") \
            .classes("text-sm font-semibold")
        ui.label(
            "На реальной сети цель прогноза — «канал сообщит о "
            "неисправности»: иначе отказ прибора, умершего вне "
            "наблюдения, просто не попал бы в разметку. В эксперименте "
            "цель — «прибор физически отказал», и она взята из физики, "
            "а не из журнала. Иначе конфигурация без самодиагностики "
            "не дала бы ни одного положительного примера и выглядела бы "
            "не провалившейся, а безупречной. Это разные величины: "
            "внутри эксперимента они сравнимы между конфигурациями, "
            "с реальными 18 % — нет."
        ).classes("text-sm")

    with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
        with ui.column().classes("flex-1 min-w-96"):
            with ui.card().classes("w-full"):
                ui.label("Прогноз отказов").classes("text-base font-semibold")
                ui.label("Полнота при одной проверке в сутки и точность "
                         "в голове очереди.").classes("text-xs opacity-70")
                ui.echart(charts.configuration_curve(
                    rows,
                    [("полнота за смену", lambda r: r["отказы"]["recall@сутки"]),
                     ("точность топ-50", lambda r: r["отказы"]["precision@50"])],
                    target=config.TARGET_RECALL,
                )).classes("w-full").style("height: 320px")
        with ui.column().classes("flex-1 min-w-96"):
            with ui.card().classes("w-full"):
                ui.label("Обнаружение задымления").classes("text-base font-semibold")
                ui.label(
                    "Тревога поднимается, только если сработали два независимых "
                    "типа датчиков. По вертикали — доля подтверждённых при "
                    "заданной физике, а не Precision сервиса: на реальных "
                    "данных разметки инцидентов нет, и эта величина там "
                    "не существует. Здесь измеряется согласие нашего "
                    "правила с нашей же объявленной физикой."
                ).classes("text-xs opacity-70")
                ui.echart(charts.configuration_curve(
                    rows,
                    [("доля подтверждённых, с подтверждением",
                      lambda r: cards.incident_value(r, "задымление",
                                               "precision_подтверждённых")),
                     ("полнота, с подтверждением",
                      lambda r: cards.incident_value(r, "задымление",
                                               "recall_подтверждённых")),
                     ("доля подтверждённых, одиночная сработка",
                      lambda r: cards.incident_value(r, "задымление", "precision"))],
                    target=config.TARGET_PRECISION,
                    # Не «цель ТЗ»: порог ТЗ задан для Precision сервиса,
                    # а здесь другая величина. Линия приведена, чтобы
                    # было видно, где достигается формальное значение,
                    # и подписана соответственно.
                    target_label=f"порог ТЗ {config.TARGET_PRECISION:.0%} "
                                 "— для справки",
                )).classes("w-full").style("height: 320px")

    # Кривые по всем трём сценариям. Без них «честная кривая
    # по сценариям» сводилась бы к одному задымлению, а подтопление
    # и проникновение были бы видны только числом в таблице целей.
    with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
        scenario_series = [
            (key, lambda r, k=key: cards.incident_value(
                r, k, "precision_подтверждённых"))
            for key in cards.SCENARIOS]
        recall_series = [
            (key, lambda r, k=key: cards.incident_value(
                r, k, "recall_подтверждённых"))
            for key in cards.SCENARIOS]
        for title, hint, series, target, label in (
            ("Доля подтверждённых по сценариям",
             "Тревога поднята двумя независимыми типами датчиков "
             "и событие действительно было.",
             scenario_series, config.TARGET_PRECISION,
             f"порог ТЗ {config.TARGET_PRECISION:.0%} — для справки"),
            ("Полнота обнаружения по сценариям",
             "Какая доля событий заданной физики поднята как "
             "подтверждённая тревога.",
             recall_series, config.TARGET_RECALL,
             f"порог ТЗ {config.TARGET_RECALL:.0%}"),
        ):
            with ui.column().classes("flex-1 min-w-96"):
                with ui.card().classes("w-full"):
                    ui.label(title).classes("text-base font-semibold")
                    ui.label(hint).classes("text-xs opacity-70")
                    ui.echart(charts.configuration_curve(
                        rows, series, target=target,
                        target_label=label,
                        colours={
                            "задымление": charts.SCENARIO["пожар"],
                            "подтопление": charts.SCENARIO["подтопление"],
                            "проникновение": charts.SCENARIO["проникновение"],
                        },
                    )).classes("w-full").style("height: 320px")

    with ui.card().classes("w-full"):
        ui.label("Все конфигурации").classes("text-base font-semibold")
        ui.table(
            columns=[
                {"name": "n", "label": "конфигурация", "field": "n", "align": "left"},
                {"name": "ch", "label": "каналов", "field": "ch"},
                {"name": "rec", "label": "отказы: полнота", "field": "rec"},
                {"name": "pre", "label": "отказы: точность", "field": "pre"},
                {"name": "fr", "label": "дым: полнота", "field": "fr"},
                {"name": "fp", "label": "дым: доля подтв. (синт.)", "field": "fp"},
                {"name": "cp", "label": "дым: то же, 2 типа", "field": "cp"},
                {"name": "cr", "label": "дым: полнота с подтв.", "field": "cr"},
                {"name": "sw", "label": "потеряно фильтром ТО",
                 "field": "sw"},
                {"name": "note", "label": "что меняется", "field": "note",
                 "align": "left"},
            ],
            rows=[{
                "n": r["конфигурация"], "ch": f"{r['каналов']:.0f}",
                "rec": f"{r['отказы']['recall@сутки']:.0%}",
                "pre": f"{r['отказы']['precision@50']:.0%}",
                "fr": f"{cards.incident_value(r, 'задымление', 'recall'):.0%}",
                "fp": f"{cards.incident_value(r, 'задымление', 'precision'):.1%}",
                "cp": f"{cards.incident_value(r, 'задымление', 'precision_подтверждённых'):.0%}",
                "cr": f"{cards.incident_value(r, 'задымление', 'recall_подтверждённых'):.0%}",
                "sw": (f"{cards.incident_value(r, 'задымление', 'событий_в_отброшенных_сутках', 0):.0f} "
                       f"из {cards.incident_value(r, 'задымление', 'событий', 0):.0f}"),
                "note": r["пояснение"],
            } for r in rows],
        ).classes("w-full").props("dense flat")

    # Прямой ответ на вопрос «достигаются ли требуемые показатели».
    # Он нужен отдельным блоком: иначе читателю придётся искать его
    # по таблице, а ответ разный для прогноза и для обнаружения.


def _improve_targets(grid, truth):
    """Где формально достигаются требуемые 0,70 и 0,50.

    Отдельным блоком, а не строкой в общей таблице: ответ разный для
    прогноза и для обнаружения, и искать его по таблице читателю
    не следует.
    """
    ui.label("Где формально достигаются 0,70 и 0,50") \
        .classes("text-lg font-semibold mt-2")
    verdict_rows = []

    def status(pairs):
        """Что именно достигнуто: оба порога, один или ни один.

        Плоское «не достигаются» прячет существенное различие. Точность
        100 % при полноте 39 % — это не то же самое, что ноль по обоим:
        в первом случае ясно, какой именно показатель упирается
        и за счёт чего его двигать.
        """
        both = [(r, p, rc) for r, p, rc in pairs
                if p >= config.TARGET_PRECISION and rc >= config.TARGET_RECALL]
        if both:
            return f"{both[0][0]['конфигурация']} — оба порога"
        only_p = [(r, p, rc) for r, p, rc in pairs
                  if p >= config.TARGET_PRECISION]
        only_r = [(r, p, rc) for r, p, rc in pairs
                  if rc >= config.TARGET_RECALL]
        if only_p and only_r:
            return (f"по частям: точность — {only_p[0][0]['конфигурация']}, "
                    f"полнота — {only_r[0][0]['конфигурация']}; "
                    "вместе не сходятся")
        if only_p:
            return (f"только точность: {only_p[0][0]['конфигурация']} "
                    f"({only_p[0][1]:.0%}), полнота не дотягивает")
        if only_r:
            return (f"только полнота: {only_r[0][0]['конфигурация']} "
                    f"({only_r[0][2]:.0%}), точность не дотягивает")
        return "ни один из двух порогов"

    fail_pairs = [(r, r["отказы"]["precision@50"], r["отказы"]["recall@сутки"])
                  for r in grid]
    best_fail = max(grid, key=lambda r: r["отказы"]["precision@50"]) if grid else None
    verdict_rows.append({
        "s": "Прогноз отказа прибора",
        "v": status(fail_pairs) if grid else "",
        "b": (f"лучшее в сетке: {best_fail['отказы']['precision@50']:.0%} точности "
              f"при полноте {best_fail['отказы']['recall@сутки']:.0%} "
              f"({best_fail['конфигурация']})") if best_fail else "",
    })

    # Число событий берётся за один прогон, а не суммарно по трём:
    # метрики усреднены по прогонам, и «4 из 18» рядом с ними читалось бы
    # как провал, хотя это 4 из 6 в каждом прогоне.
    events_of = {key: (cards.incident_value(grid[0], key, "событий", 0)
                       if grid else 0)
                 for key in cards.SCENARIOS}
    for key, label in (("задымление", "Обнаружение задымления"),
                       ("подтопление", "Обнаружение подтопления"),
                       ("проникновение", "Обнаружение проникновения")):
        def pair(r, k=key):
            return (cards.incident_value(r, k, "precision_подтверждённых"),
                    cards.incident_value(r, k, "recall_подтверждённых"))
        pairs = [(r, pair(r)[0], pair(r)[1]) for r in grid]
        hit = [r for r in grid
               if pair(r)[0] >= config.TARGET_PRECISION
               and pair(r)[1] >= config.TARGET_RECALL]
        top = max(grid, key=lambda r: sum(pair(r))) if grid else None

        def absolute(r, k=key):
            """Проценты без абсолютных чисел здесь обманчивы: «100 %»
            может означать одно подтверждённое событие из одного.

            Числа усреднены по прогонам, поэтому дробные: «0,3 события
            в сутки прогона ТО» означает «в одном прогоне из трёх одно
            событие попало в отброшенные сутки». Округлять до целого
            нельзя — потеря исчезла бы из отчёта.
            """
            conf = cards.incident_value(r, k, "подтверждено_вторым_типом", 0)
            total = cards.incident_value(r, k, "событий", 0)
            lost = cards.incident_value(r, k,
                                        "событий_в_отброшенных_сутках", 0)
            tail = (f", {lost:.1f} в сутки прогона ТО".replace(".", ",")
                    if lost >= 0.05 else "")
            return f"{conf:.0f} из {total:.0f} событий{tail}"

        verdict_rows.append({
            "s": f"{label} ({events_of[key]:.0f} за прогон)",
            "v": (f"{hit[0]['конфигурация']} — {absolute(hit[0])}"
                  if hit else status(pairs)),
            "b": (f"лучшее в сетке: {pair(top)[0]:.0%} доли подтверждённых "
                  f"при полноте {pair(top)[1]:.0%} — {absolute(top)} "
                  f"({top['конфигурация']})")
                 if top else "",
        })

    achieved = [r for r in verdict_rows
                if "оба порога" in r["v"] or "событий" in r["v"]]
    with ui.card().classes(
            "w-full border-l-4 border-"
            + ("emerald" if achieved else "amber") + "-500"):
        ui.table(
            columns=[{"name": "s", "label": "сценарий", "field": "s",
                      "align": "left"},
                     {"name": "v", "label": "где достигаются", "field": "v",
                      "align": "left"},
                     {"name": "b", "label": "иначе — лучшее в сетке",
                      "field": "b", "align": "left"}],
            rows=verdict_rows,
        ).classes("w-full").props("dense flat")
        ui.separator()
        ui.label("Как это читать").classes("text-sm font-semibold")
        ui.label(
            "Для прогноза отказа потолок задан не оснащением, а долей "
            "отказов, которые мы сами объявили не имеющими наблюдаемых "
            "предвестников. Поэтому «не достигаются» здесь означает "
            "«не достигаются в этой сетке при этих допущениях», а не "
            "«недостижимы никаким набором датчиков» — такого утверждения "
            "эксперимент не доказывает. "
            + ("Для обнаружения инцидентов достигнутые значения — это "
               "доля подтверждённых при заданной физике, а не Precision "
               "сервиса: на реальных данных разметки инцидентов нет, "
               "и там эта величина не существует."
               if achieved else
               "Ни одна конфигурация сетки не вышла на оба требуемых "
               "значения одновременно. В графе «где достигаются» "
               "сказано, какой именно порог взят и какой упирается — "
               "это и есть ответ на вопрос, что двигать дальше.")
        ).classes("text-sm")
        ui.label(
            "Число событий указано рядом со сценарием и рядом с каждой "
            "цифрой, и указано за один прогон: метрики усреднены по трём "
            "прогонам с разными зёрнами, поэтому сопоставлять их надо "
            "с событиями одного прогона. Выборки малы, полнота по ним "
            "дискретна, и проценты здесь без абсолютных чисел читать "
            "нельзя. Это ограничение эксперимента, а не оценка сервиса."
        ).classes("text-xs opacity-70")


def _improve_sweep(grid):
    """Фильтр регламентных прогонов и его цена.

    Содержательная находка, а не техническая деталь: без фильтра обход
    техника выглядит идеальным подтверждением и топит точность.
    """
    fire_row = next((r for r in grid
                     if cards.incident_value(r, "задымление", "суток_прогона_ТО", 0)),
                    None)
    if fire_row:
        inc = fire_row["инциденты"]["задымление"]
        with ui.card().classes("w-full border-l-4 border-slate-500"):
            ui.label("Почему обход техника приходится отбрасывать") \
                .classes("text-base font-semibold")
            ui.label(
                "Во время регламентного обхода техник проверяет датчики, "
                "и срабатывают сразу оба типа — дымовой и тепловой, в одной "
                "точке, в одно время. Для правила «два независимых типа "
                "подтвердили друг друга» это выглядит идеальным "
                "подтверждением. Поэтому каждый обход попадал бы в ложные "
                "подтверждённые тревоги: измерено, что без фильтра доля "
                "подтверждённых получается 2 % вместо 100 %."
            ).classes("text-sm opacity-80")
            ui.label(
                "Эксперимент берёт критерий массовости самого сервиса, "
                "а не своё правило. На реальных данных он там не случайно: "
                "у одной базы зафиксировано 27 суток, когда разом "
                "срабатывали все 96 дымовых каналов по всем 45 пикетам."
            ).classes("text-sm")
            with ui.row().classes("items-baseline gap-4 mt-1"):
                ui.label(plural.count(inc.get("суток_прогона_ТО", 0),
                                      "сутки прогона", "суток прогона",
                                      "суток прогона")) \
                    .classes("text-sm font-semibold")
                ui.label(plural.count(inc.get("сработок_отброшено_как_ТО", 0),
                                      "сработка отброшена",
                                      "сработки отброшено",
                                      "сработок отброшено")) \
                    .classes("text-sm opacity-70")
            ui.label(
                "Фильтр стоит денег, и это сказано прямо: вместе "
                "с прогонами теряются настоящие события, случившиеся "
                "в те же сутки. Поэтому число отброшенного показано, "
                "а не умолчано."
            ).classes("text-xs opacity-70")
            ui.label(
                "Тонкость, о которой стоит знать: критерий массовости "
                "считается по доле каналов базы, а число каналов "
                "на контроллер — одна из варьируемых характеристик. "
                "Значит агрессивность фильтра зависит от конфигурации. "
                "Часть этого эффекта содержательна — на густо "
                "оснащённом контроллере прогон распознаётся надёжнее, — "
                "но часть является поведением фильтра, а не способностью "
                "обнаруживать. Поэтому рядом с каждой цифрой стоит число "
                "настоящих событий, попавших в отброшенные сутки."
            ).classes("text-xs opacity-70")


def _improve_placebo(base_row, placebo):
    """Отрицательный контроль: что сдвигает бесполезный датчик.

    Без этого блока сетка не доказывает ничего: три признака
    из двадцати — счётчик каналов в точке и средние по ним, поэтому
    добавление любого прибора меняет вход модели арифметически.
    """
    if base_row and placebo:
        delta = (placebo["отказы"]["precision@50"]
                 - base_row["отказы"]["precision@50"])
        with ui.card().classes("w-full border-l-4 border-slate-500"):
            ui.label("Отрицательный контроль: плацебо").classes(
                "text-base font-semibold")
            ui.label(
                "В конфигурации «плацебо» на каждый пикет добавлен датчик "
                "движения. О здоровье дымового прибора он не знает ничего, "
                "но удваивает число каналов в точке и на контроллере — то "
                "есть меняет знаменатели трёх признаков из двадцати. "
                "Поэтому его эффект — это чистая арифметика, и любой "
                "прирост меньше него содержательным считать нельзя."
            ).classes("text-sm opacity-80")
            with ui.row().classes("items-baseline gap-3 mt-1"):
                ui.label(f"{delta:+.0%}").classes(
                    "text-2xl font-bold "
                    + ("text-rose-600" if abs(delta) >= 0.02 else "text-emerald-600"))
                ui.label("сдвиг точности топ-50 от бесполезного датчика") \
                    .classes("text-sm opacity-70")
            ui.label(
                "Порог содержательности: прирост конфигурации считается "
                f"реальным, только если он больше {abs(delta):.0%}."
                if abs(delta) >= 0.01 else
                "Сдвиг в пределах шума: знаменатели признаков на результат "
                "существенно не влияют, и прирост конфигураций можно "
                "читать как содержательный."
            ).classes("text-sm")


def _improve_conclusions(grid, base_row, floor):
    """Что из сетки следует, с порогом содержательности от плацебо.

    Без этого блока читатель сложит выводы сам, и скорее всего прочтёт
    прирост в пределах арифметики как улучшение.
    """
    quiet = next((r for r in grid
                  if r["конфигурация"] == "без самодиагностики"), None)
    if base_row and quiet:
        drop = (base_row["отказы"]["recall@сутки"]
                - quiet["отказы"]["recall@сутки"])
        confirmed = max(
            (cards.incident_value(r, "задымление",
                                  "precision_подтверждённых")
             for r in grid), default=0.0)
        ui.label("Что из этого следует").classes("text-lg font-semibold mt-2")
        with ui.card().classes("w-full border-l-4 border-slate-700"):
            for title, text in (
                ("Самодиагностика прибора — необходимое условие",
                 "Без неё прогноз отказа обращается в ноль, а не "
                 f"ухудшается: полнота за смену падает на {drop:.0%}."
                 + (f" Это кратно больше плацебо-сдвига ({floor:.0%}), "
                    "то есть эффект настоящий."
                    if floor > 0 else
                    " Плацебо-конфигурация в этой сетке не посчитана, "
                    "поэтому порог содержательности не предъявлен.")
                 + " Приборы типа «сухой контакт» не дают модели "
                   "ни одного наблюдаемого предвестника."),
                ("Одним типом датчика подтверждение невозможно "
                 "в принципе",
                 "Пока в точке один тип, доля подтверждённых тревог "
                 "равна нулю при любой плотности установки и любой "
                 "периодичности опроса: подтверждать нечем."
                 + (f" Со вторым релевантным типом она становится "
                    f"{confirmed:.0%}. Эффект категориальный, "
                    "а не количественный, и потому к допущениям "
                    "генератора нечувствителен."
                    if confirmed > 0 else
                    " Конфигурации со вторым релевантным типом в этой "
                    "сетке не посчитаны, поэтому вторая половина "
                    "утверждения не предъявлена.")),
                ("Дооснащение прогноз отказа не улучшает — "
                 "это главный отрицательный результат",
                 ("Ни одна ось не даёт прироста больше плацебо-сдвига. "
                  if floor > 0 else
                  "Прирост ни одной оси не отличается от шума. ")
                 + "Это согласуется с измерением на реальной сети, где "
                   "признаки соседних датчиков прогнозу тоже не помогают. "
                   "Поломку прибора предсказывает его собственная "
                   "история, и купить её установкой других приборов "
                   "нельзя."),
            ):
                with ui.column().classes("gap-0 mt-2"):
                    ui.label(title).classes("text-sm font-semibold")
                    ui.label(text).classes("text-sm opacity-85")

            ui.separator()
            ui.label("Поэтому ответ распадается надвое").classes(
                "text-sm font-semibold")
            ui.label(
                "Для прогноза отказа решают характеристики самих "
                "приборов, а не их количество: прибор обязан уметь "
                "сообщать о своей неисправности. Это требование "
                "к спецификации закупки, а не к схеме расстановки."
            ).classes("text-sm")
            ui.label(
                "Для обнаружения инцидентов решает набор типов в точке: "
                "нужен второй независимый тип, физически способный "
                "подтвердить тот же процесс. Тепловой датчик "
                "подтверждает дым, датчик затопления — работу насоса, "
                "датчик движения — открытие люка. Датчик движения дым "
                "не подтверждает ни при какой плотности, и «больше "
                "датчиков» с «нужные датчики» — разные вещи."
            ).classes("text-sm")


def _improve_recommendation(rows):
    """Итоговая рекомендация: что поставить и с какими характеристиками."""
    # --- часть третья: рекомендация
    ui.label("Что поставить").classes("text-lg font-semibold mt-2")
    best = cards.recommended(rows)
    if best:
        with ui.card().classes("w-full border-l-4 border-emerald-500"):
            ui.label(f"Рекомендуемая конфигурация: {best['конфигурация']}") \
                .classes("text-base font-semibold")
            ui.label(best["пояснение"]).classes("text-sm opacity-80")
            met = cards.targets_met(best)
            ui.label(
                "Выбрана как самая дешёвая из тех, что закрывают оба "
                "порога ТЗ по "
                + plural.count(met, "сценарию", "сценариям", "сценариям")
                + " обнаружения."
                if met else
                "Ни одна конфигурация сетки не закрывает оба порога ТЗ "
                "ни по одному сценарию, поэтому выбрана самая дешёвая "
                "из дающих наибольшую суммарную пользу. Это слабее, чем "
                "ответ на вопрос организаторов, и названо прямо."
            ).classes("text-sm " + ("opacity-80" if met
                                    else "text-amber-800 font-semibold"))
            ui.separator()
            ui.table(
                columns=[{"name": "k", "label": "характеристика", "field": "k",
                          "align": "left"},
                         {"name": "v", "label": "значение", "field": "v",
                          "align": "left"}],
                rows=[
                    {"k": "Набор датчиков и шаг установки",
                     # Значения усреднены по прогонам и приходят дробными,
                     # хотя шаг установки целый по смыслу.
                     "v": ", ".join(f"{k} — каждый {v:.0f}-й пикет"
                                    for k, v in best["датчиков"].items())},
                    {"k": "Периодичность опроса",
                     "v": ", ".join(f"{k} — раз в {v:.0f} мин"
                                    for k, v in best["опрос_минут"].items())
                          or "событийный режим"},
                    {"k": "Самодиагностика прибора",
                     "v": "обязательна" if best["самодиагностика"] else "нет"},
                    {"k": "Контроль линии связи",
                     "v": "обязателен" if best["контроль_линии"] else "нет"},
                    {"k": "Каналов на контроллер",
                     "v": f"{best['каналов_на_контроллер']:.0f} — чтобы был виден каскад"},
                    {"k": "Порог срабатывания (точность измерения)",
                     "v": thresholds_phrase(best)},
                ],
            ).classes("w-full").props("dense flat")



@ui.page("/improve")
def improve_page():
    """Что поставить на объекте, чтобы прогноз стал точнее.

    Страница отвечает на вопрос организаторов и делает это по порядку:
    что измеряет нынешняя метрика, что измерено на реальной сети, что
    показал эксперимент на синтетическом объекте, где достигаются
    требуемые показатели и что из этого следует поставить. Порядок
    существенен, поэтому разделы вынесены отдельными функциями и здесь
    видна только последовательность.
    """
    if not require_login():
        return
    header()
    experiment = load_json_file(EXPERIMENT)
    calibration = load_json_file(CALIBRATION)
    controls = load_json_file(CONTROLS)

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        ui.label("Повышение точности прогноза").classes("text-xl font-semibold")
        ui.label(
            "Какой набор датчиков и с какими характеристиками нужно поставить "
            "на объекте, чтобы точность прогноза выросла. Ответ собран "
            "в три шага: что измеряет нынешняя метрика, что измерено "
            "на реальной сети и что показал эксперимент на синтетическом "
            "объекте, где у инцидентов есть разметка."
        ).classes("text-sm opacity-70")

        _improve_what_is_predicted(controls)
        _improve_measured(calibration)

        ui.label("Эксперимент на синтетическом объекте").classes(
            "text-lg font-semibold mt-2")
        if not experiment:
            ui.label("Результаты эксперимента не рассчитаны. "
                     "Запустите scripts/12_sensor_experiment.").classes("opacity-60")
            return

        rows = experiment["конфигурации"]
        physics = experiment["физика"]
        truth = cards.experiment_truth(physics)

        # Контрольные конфигурации из сетки сравнения исключаются: их задача
        # показать размер арифметического сдвига, а не быть вариантом выбора.
        grid = [r for r in rows if not r["конфигурация"].startswith("плацебо")]
        base_row = next((r for r in rows
                         if r["конфигурация"] == "как сейчас"), None)
        placebo = next((r for r in rows
                        if r["конфигурация"].startswith("плацебо")), None)
        floor = (abs(placebo["отказы"]["precision@50"]
                     - base_row["отказы"]["precision@50"])
                 if base_row and placebo else 0.0)

        _improve_experiment(rows, physics, truth)
        _improve_targets(grid, truth)
        _improve_sweep(grid)
        _improve_placebo(base_row, placebo)
        _improve_conclusions(grid, base_row, floor)
        _improve_recommendation(rows)

        with ui.card().classes("bg-slate-100 w-full p-3"):
            ui.label("Оговорка о синтетике").classes("text-sm font-semibold")
            ui.label(
                "Экспериментальный объект целиком синтетический: это отдельный "
                "объект с собственными идентификаторами, он исключён из всех "
                "метрик реальной сети, отчётов и журнала прогнозов. В очереди "
                "его вердикты помечены отдельно, и зафиксировать по ним "
                "решение нельзя. Распределения генератора сняты с реальных "
                "данных и лежат в models/sensor_calibration.json, но сам "
                "объект в природе не существует."
            ).classes("text-sm")


# ------------------------------------------------------------- REST API

# Токен доступа к API. По умолчанию пуст, и тогда API открыт: данные
# обезличены на стороне источника, интерфейс только на чтение, а эксперту,
# проверяющему решение, не нужен секрет, о котором он не знает. Если токен
# задан переменной окружения, он спрашивается со всех эндпоинтов, кроме
# проверки живости — её должен видеть оркестратор.
API_TOKEN = os.environ.get("API_TOKEN", "")

# Значение HTTP-заголовка на проводе — latin-1, поэтому кириллический токен
# до сервера доедет искажённым и не совпадёт никогда. Молча отдавать 401 на
# верный, с точки зрения администратора, токен — худшее из поведений: ошибка
# выглядит как поломка сервиса. Поэтому проверка на старте и внятный отказ.
if API_TOKEN and not API_TOKEN.isascii():
    raise SystemExit(
        "API_TOKEN должен состоять из символов ASCII: значения HTTP-заголовков "
        "передаются в latin-1, и токен с кириллицей не дойдёт неискажённым.")


def check_token(request):
    """Проверка токена. Возвращает отказ или None, если доступ разрешён.

    Отказ возвращается, а не выбрасывается исключением: обработчик ошибок
    интерфейса отрисовал бы клиенту API страницу на четверть мегабайта
    вместо короткого JSON, который тот умеет разбирать.
    """
    if not API_TOKEN:
        return None
    supplied = request.headers.get("x-api-token") or ""
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    # Сравнение постоянного времени: посимвольное сравнение строк выдаёт
    # длину совпавшего префикса разницей во времени ответа. Сравниваются
    # байты, а не строки: compare_digest не принимает строки с кириллицей,
    # а токен вполне может оказаться русским словом.
    if hmac.compare_digest(supplied.encode("utf-8"), API_TOKEN.encode("utf-8")):
        return None
    return JSONResponse(status_code=401,
                        content={"error": "требуется заголовок X-API-Token"})


def api_rows(scenario=None, status=None, limit=100):
    """Вердикты для выгрузки в любом формате — один источник на все четыре."""
    con = get_con()
    if con is None:
        rows, _ = load_verdicts(limit)
        return rows
    return db.fetch_verdicts(con, status=status, scenario=scenario, limit=limit)


def attachment(data, media_type, filename):
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})


@app.get("/api/verdicts")
def api_verdicts(request: Request, scenario: str = None, status: str = None,
                 limit: int = 100):
    """Вердикты для внешних систем — требование ТЗ о REST-интерфейсе."""
    denied = check_token(request)
    if denied is not None:
        return denied
    rows = api_rows(scenario, status, limit)
    for r in rows:
        created = r.get("created_at")
        r["created_at"] = created.isoformat() if hasattr(created, "isoformat") else created
    return {"count": len(rows), "items": rows}


@app.get("/api/verdicts.xml")
def api_verdicts_xml(request: Request, scenario: str = None, status: str = None,
                     limit: int = 100):
    """Те же вердикты в XML — раздел 7 ТЗ требует оба формата обмена."""
    denied = check_token(request)
    if denied is not None:
        return denied
    return Response(content=exchange.to_xml(api_rows(scenario, status, limit)),
                    media_type="application/xml; charset=utf-8")


@app.get("/api/verdicts.csv")
def api_verdicts_csv(request: Request, scenario: str = None, status: str = None,
                     limit: int = 1000):
    """Плоская выгрузка вердиктов — файловый обмен CSV, раздел 7 ТЗ."""
    denied = check_token(request)
    if denied is not None:
        return denied
    return attachment(exchange.to_csv(api_rows(scenario, status, limit)),
                      "text/csv; charset=utf-8", "concorde_verdicts.csv")


@app.get("/api/geometry.geojson")
def api_geometry_geojson(request: Request):
    """Геометрия объектов и точки вердиктов — геоданные в GeoJSON, раздел 7 ТЗ."""
    denied = check_token(request)
    if denied is not None:
        return denied
    payload = json.dumps(exchange.to_geojson(load_geometry()), ensure_ascii=False)
    return Response(content=payload.encode("utf-8"),
                    media_type="application/geo+json; charset=utf-8")


@app.get("/api/geometry.wkt")
def api_geometry_wkt(request: Request):
    """Та же геометрия в Well-Known Text — второй требуемый геоформат."""
    denied = check_token(request)
    if denied is not None:
        return denied
    return attachment(exchange.to_wkt(load_geometry()),
                      "text/csv; charset=utf-8", "concorde_geometry_wkt.csv")


@app.get("/api/report.xlsx")
def api_report(request: Request):
    """Выгрузка отчёта для руководства — требование ТЗ по форматам XLSX."""
    denied = check_token(request)
    if denied is not None:
        return denied
    con = get_con()
    if con is None:
        return Response(content=b"", status_code=503)
    data = reports.to_bytes(reports.build_report(con))
    return attachment(
        data,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "concorde_report.xlsx")


@app.get("/api/health")
def api_health():
    """Живость сервиса. Токеном не закрывается: её опрашивает оркестратор."""
    con = get_con()
    return {"status": "ok", "database": con is not None,
            "api_token_required": bool(API_TOKEN)}


def main():
    ui.run(host="0.0.0.0", port=8080, title="Дискреция творца", reload=False,
           favicon="🛠", dark=False, show=False,
           storage_secret=os.environ.get("SESSION_SECRET", "concorde-demo-secret"))


main()
