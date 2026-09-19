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

from core import auth, cards, config, db, explain, orders, reports

SEED = os.path.join(config.ROOT, "deploy", "seed_verdicts.json")
GEOMETRY = os.path.join(config.ROOT, "deploy", "seed_geometry.json")

SCENARIO_HEX = {
    "отказ": "#f59e0b",
    "подтопление": "#3b82f6",
    "пожар": "#ef4444",
    "проникновение": "#a855f7",
}

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

        with ui.row().classes("w-full justify-end gap-2"):
            if new:
                ui.button("Квитировать", icon="check",
                          on_click=lambda vid=v["id"]: do_ack(vid)).props("outline dense")
            ui.button("Решение", icon="assignment",
                      on_click=lambda vid=v["id"]: decision_dialog(vid)).props("dense")
            ui.button("Заявка", icon="construction",
                      on_click=lambda vv=v: work_order_dialog(vv)).props("outline dense")


def do_ack(verdict_id):
    con = get_con()
    if con is None:
        ui.notify("Оперативный контур недоступен — режим просмотра", type="warning")
        return
    db.acknowledge(con, verdict_id, (current_user() or {}).get("id"))
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
            ui.label(f"{len(geometry)} объектов").classes("text-sm opacity-60")

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
                     "color": SCENARIO_HEX.get(p["scenario"], "#94a3b8"),
                     "fillColor": SCENARIO_HEX.get(p["scenario"], "#94a3b8"),
                     "fillOpacity": 0.8, "weight": 1},
                ])

        with ui.row().classes("gap-4 text-xs opacity-75 flex-wrap"):
            for label, color in (("отказ датчика", "#f59e0b"),
                                 ("подтопление", "#3b82f6"),
                                 ("задымление", "#ef4444"),
                                 ("проникновение", "#a855f7")):
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
                            f"{g['length_m'] / 1000:.1f} км · {g['n_cards']} вердиктов"
                        ).classes("text-xs opacity-60")
                    ui.label(f"{g['max_probability']:.0%}").classes(
                        f"text-lg font-bold text-{risk_color(g['max_probability'])}-600")


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


@app.get("/api/report.xlsx")
def api_report():
    """Выгрузка отчёта для руководства — требование ТЗ по форматам XLSX."""
    from fastapi.responses import Response
    con = get_con()
    if con is None:
        return Response(content=b"", status_code=503)
    data = reports.to_bytes(reports.build_report(con))
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="concorde_report.xlsx"'},
    )


@app.get("/api/health")
def api_health():
    con = get_con()
    return {"status": "ok", "database": con is not None}


def main():
    ui.run(host="0.0.0.0", port=8080, title="Дискреция творца", reload=False,
           favicon="🛠", dark=False, show=False,
           storage_secret=os.environ.get("SESSION_SECRET", "concorde-demo-secret"))


main()
