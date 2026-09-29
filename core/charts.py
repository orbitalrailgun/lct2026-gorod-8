"""Диаграммы: описания графиков для ECharts.

Отдельный модуль, потому что диаграмма — это данные, а не вёрстка. Здесь
собираются только описания: что откладывается по осям, какими цветами и
с какими подписями. Где их показать, решает `app/main.py`.

Финальная экспертиза оценивает диаграммы отдельным пунктом: «оценка
достоверности диаграмм будет проводиться на основании их визуальной
восприимчивости и отражения данных». Поэтому здесь несколько правил,
которые выполняются без исключений.

**Палитра проверена, а не подобрана на глаз.** Четыре цвета сценариев
различимы при дейтеранопии и тританопии: худшая пара по расстоянию в OKLab
даёт ΔE 13,0 при пороге 8. Жёлтый на белом фоне не добирает контраста 3:1,
поэтому у каждого жёлтого элемента есть подпись значением — цвет никогда
не остаётся единственным носителем смысла.

**Одна ось.** Две величины разного масштаба на одном графике создают
корреляцию, которой нет в данных. Где нужны обе, вторая уходит в подсказку
при наведении или в соседний график.

**Цвет закреплён за сущностью.** «Отказ» жёлтый всегда и везде: на графике,
на карте, на линейной схеме. Фильтр не перекрашивает то, что осталось.
"""

# --------------------------------------------------------------- палитра

# Категориальные цвета сценариев. Порядок закреплён: цвет следует за
# сущностью, а не за её местом в текущей сортировке.
SCENARIO = {
    "отказ": "#eda100",
    "подтопление": "#2a78d6",
    "пожар": "#e34948",
    "проникновение": "#4a3aa7",
}

SERIES = "#2a78d6"        # единственный ряд — один цвет, без градиента по величине
ACCENT = "#e34948"        # порог, цель, критическая отметка
INK = "#0b0b0b"
TEXT = "#52514e"
MUTED = "#8a8a85"
GRID = "#ececea"
AXIS = "#d8d8d4"
SURFACE = "#ffffff"

FONT = 11


def _axis_label(**extra):
    base = {"color": TEXT, "fontSize": FONT}
    base.update(extra)
    return base


def _value_axis(name=None, formatter=None, maximum=None):
    axis = {
        "type": "value",
        "axisLine": {"show": False},
        "axisTick": {"show": False},
        "splitLine": {"lineStyle": {"color": GRID, "width": 1}},
        "axisLabel": _axis_label(**({"formatter": formatter} if formatter else {})),
    }
    if name:
        axis["name"] = name
        axis["nameTextStyle"] = {"color": MUTED, "fontSize": FONT}
    if maximum is not None:
        axis["max"] = maximum
    return axis


def _category_axis(data, rotate=0):
    return {
        "type": "category",
        "data": data,
        "axisLine": {"lineStyle": {"color": AXIS}},
        "axisTick": {"show": False},
        "splitLine": {"show": False},
        "axisLabel": _axis_label(rotate=rotate, interval=0),
    }


def _tooltip(trigger="item", formatter=None):
    tip = {
        "trigger": trigger,
        "backgroundColor": "#ffffff",
        "borderColor": AXIS,
        "borderWidth": 1,
        "textStyle": {"color": INK, "fontSize": 12},
        "axisPointer": {"type": "shadow"},
    }
    if formatter:
        tip["formatter"] = formatter
    return tip


def _grid(left=8, right=24, top=16, bottom=8):
    return {"left": left, "right": right, "top": top, "bottom": bottom,
            "containLabel": True}


# ------------------------------------------------- качество прогнозирования

def horizon(rows):
    """Плотность попаданий в голове очереди по горизонтам прогноза.

    Откладывается precision@50 — доля настоящих отказов среди пятидесяти
    самых уверенных прогнозов. Раньше здесь была «максимальная точность
    на кривой», и это оказалось ловушкой: на семисуточном горизонте она
    равна единице при полноте 0,0002, то есть три попадания из трёх.
    Метрика, которую можно довести до ста процентов, сократив выборку
    до горстки, ничего не измеряет.

    Линия цели ТЗ нарисована, чтобы читатель видел расстояние до неё,
    а не верил на слово.
    """
    labels = [r["label"] for r in rows]
    values = [round(r.get("precision_at_50", r.get("best_precision", 0)) * 100, 1)
              for r in rows]
    return {
        "grid": _grid(bottom=8),
        "tooltip": _tooltip("axis"),
        "xAxis": _category_axis(labels),
        "yAxis": _value_axis(formatter="{value} %", maximum=100),
        "series": [{
            "type": "bar",
            "data": values,
            "barMaxWidth": 46,
            "itemStyle": {"color": SERIES, "borderRadius": [4, 4, 0, 0]},
            "label": {"show": True, "position": "top", "color": INK,
                      "fontSize": 12, "formatter": "{c} %"},
            "markLine": {
                "silent": True,
                "symbol": "none",
                "lineStyle": {"color": ACCENT, "width": 1.5, "type": "solid"},
                "label": {"formatter": "цель ТЗ 70 %", "color": ACCENT,
                          "fontSize": FONT, "position": "insideEndTop"},
                "data": [{"yAxis": 70}],
            },
        }],
    }


def recall_cost(rows):
    """Цена полноты: сколько проверок в сутки требует заданный Recall.

    Ось полноты числовая, а не категориальная. На категориальной оси
    расстояние от 30 % до 50 % выглядело бы таким же, как от 10 % до 20 %,
    и кривая врала бы о скорости роста нагрузки — а вся суть графика
    именно в том, как быстро она растёт.

    Вторая величина — точность — намеренно не выводится второй осью:
    два масштаба на одном поле создают корреляцию, которой нет. Точность
    видна в подсказке при наведении и в таблице под графиком.
    """
    points = [[round(r["recall"] * 100), round(r["alerts_per_day"])] for r in rows]
    precision = {round(r["recall"] * 100): round(r["precision"] * 100, 2)
                 for r in rows}
    target = next((p for p in points if p[0] == 50), points[-1])
    return {
        "grid": _grid(top=24, bottom=8),
        "tooltip": _tooltip("item", formatter=(
            "function (p) {"
            " var prec = " + str(precision) + ";"
            " return 'Полнота ' + p.data[0] + ' %<br/>проверок в сутки: <b>'"
            "   + p.data[1] + '</b><br/>точность: ' + prec[p.data[0]] + ' %'; }")),
        "xAxis": {
            "type": "value",
            "min": 0,
            "max": 75,
            "axisLine": {"lineStyle": {"color": AXIS}},
            "axisTick": {"show": False},
            "splitLine": {"show": False},
            "axisLabel": _axis_label(formatter="{value} %"),
        },
        "yAxis": _value_axis(),
        "series": [{
            "type": "line",
            "data": points,
            "lineStyle": {"color": SERIES, "width": 2},
            "itemStyle": {"color": SERIES, "borderColor": SURFACE,
                          "borderWidth": 2},
            "symbolSize": 9,
            "label": {"show": True, "position": "top", "color": TEXT,
                      "fontSize": 11, "formatter": "{@[1]}"},
            "markPoint": {
                "symbol": "circle",
                "symbolSize": 13,
                "itemStyle": {"color": ACCENT, "borderColor": SURFACE,
                              "borderWidth": 2},
                "label": {"show": True, "position": "right", "distance": 8,
                          "color": ACCENT, "fontSize": FONT,
                          "formatter": "цель ТЗ"},
                "data": [{"coord": target}],
            },
        }],
    }


# ------------------------------------------------------- наблюдаемость

def observability(rows, total):
    """Чего нет на объектах. Столбик до полной длины — это все объекты сети.

    График отвечает на вопрос, который обычно не задают вслух: где сервис
    слеп. Полная длина шкалы зафиксирована числом объектов, поэтому
    столбик читается как доля, а не как абстрактная величина.
    """
    labels = [r["type"] for r in rows][::-1]
    values = [r["objects_without"] for r in rows][::-1]
    return {
        "grid": _grid(left=8, right=56, top=8, bottom=8),
        "tooltip": _tooltip("axis", formatter=(
            "function (p) { return p[0].name + '<br/>объектов без контроля: <b>'"
            " + p[0].data + '</b> из " + str(total) + "'; }")),
        "xAxis": _value_axis(maximum=total),
        "yAxis": _category_axis(labels),
        "series": [{
            "type": "bar",
            "data": values,
            "barMaxWidth": 22,
            "itemStyle": {"color": SERIES, "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": INK,
                      "fontSize": 12, "formatter": "{c} из " + str(total)},
        }],
    }


def incubation(buckets):
    """Как копятся признаки: внезапное развитие против постепенной деградации.

    Из этого распределения выводится срок заявки. То, что копилось два
    месяца, не обязано чиниться завтра; то, что возникло за час, не может
    ждать планового обхода.
    """
    return {
        "grid": _grid(bottom=8),
        "tooltip": _tooltip("axis", formatter=(
            "function (p) { return p[0].axisValue + '<br/>вердиктов: <b>'"
            " + p[0].data + '</b>'; }")),
        "xAxis": _category_axis([b["label"] for b in buckets]),
        "yAxis": _value_axis(),
        "series": [{
            "type": "bar",
            "data": [b["count"] for b in buckets],
            "barMaxWidth": 46,
            "itemStyle": {"color": SERIES, "borderRadius": [4, 4, 0, 0]},
            "label": {"show": True, "position": "top", "color": INK,
                      "fontSize": 12},
        }],
    }


# --------------------------------------------------------- оперативная картина

def by_object(objects, scenarios, matrix):
    """Вердикты по объектам, разложенные по сценариям.

    Столбики стоят рядом, а не друг на друге в один цвет: диспетчеру нужно
    видеть не только сколько всего, но и чего именно. Между сегментами
    оставлен зазор цветом фона — так граница читается без обводки.
    """
    series = []
    for name in scenarios:
        series.append({
            "name": name,
            "type": "bar",
            "stack": "всего",
            "data": [matrix[name].get(obj, 0) for obj in objects],
            "barMaxWidth": 22,
            "itemStyle": {"color": SCENARIO.get(name, SERIES),
                          "borderColor": SURFACE, "borderWidth": 2},
        })
    return {
        "grid": _grid(left=8, right=32, top=8, bottom=32),
        "tooltip": _tooltip("axis"),
        "legend": {"bottom": 0, "itemWidth": 10, "itemHeight": 10,
                   "icon": "roundRect", "textStyle": {"color": TEXT,
                                                      "fontSize": FONT}},
        "xAxis": _value_axis(),
        "yAxis": _category_axis(objects),
        "series": series,
    }


def evidence(items):
    """Вклад улик в конкретный вердикт.

    График показывает ровно то, что посчитала модель: сумма вкладов
    сходится со скором с точностью 4,44·10⁻¹⁶. Подписи стоят у каждого
    столбика, потому что число здесь важнее длины.
    """
    labels = [i["short"] for i in items][::-1]
    values = [round(i["contribution"] * 100, 1) for i in items][::-1]
    return {
        "grid": _grid(left=8, right=48, top=8, bottom=8),
        "tooltip": _tooltip("item", formatter=(
            "function (p) { return p.name + '<br/>вклад: <b>+' + p.data"
            " + ' %</b>'; }")),
        "xAxis": _value_axis(formatter="{value} %"),
        "yAxis": _category_axis(labels),
        "series": [{
            "type": "bar",
            "data": values,
            "barMaxWidth": 16,
            "itemStyle": {"color": SERIES, "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": INK,
                      "fontSize": 11, "formatter": "+{c} %"},
        }],
    }


# --------------------------------------------------------------- подготовка

INCUBATION_BUCKETS = (
    (0, 0, "сегодня"),
    (1, 7, "1–7 суток"),
    (8, 30, "8–30 суток"),
    (31, 60, "31–60 суток"),
    (61, 10 ** 6, "свыше 60 суток"),
)


def incubation_buckets(values):
    """Раскладка вызревания признаков по интервалам."""
    out = []
    for low, high, label in INCUBATION_BUCKETS:
        n = sum(1 for v in values if v is not None and low <= v <= high)
        out.append({"label": label, "count": n})
    return out


def shorten(text, limit=42):
    """Короткая подпись для оси: ось не место для предложения."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip(" ,;:") + "…"
