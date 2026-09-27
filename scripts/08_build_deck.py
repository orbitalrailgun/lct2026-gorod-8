"""Сборка прототипа презентации.

ТЗ, раздел 15: презентация в формате pptx или pdf. Здесь собирается pptx,
чтобы команда правила его в обычном редакторе.

Почему кодом, а не руками: числа в презентации обязаны совпадать с числами
в записке и в модели. Собранный скриптом слайд нельзя случайно оставить
со старой метрикой после переобучения — пересборка занимает секунду.

Места под скриншоты обозначены рамками с подписью: их вставляет команда,
потому что снимать интерфейс лучше вживую, с настоящей карточкой на экране.

Запуск:

    python -m scripts.08_build_deck
"""

import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from core import config

TARGET = os.path.join(config.ROOT, "docs", "презентация-прототип.pptx")

# ---------------------------------------------------------------- оформление

INK = RGBColor(0x0F, 0x17, 0x2A)
MUTED = RGBColor(0x47, 0x55, 0x69)
FAINT = RGBColor(0x94, 0xA3, 0xB8)
ACCENT = RGBColor(0xC2, 0x41, 0x0C)
GOOD = RGBColor(0x15, 0x80, 0x3D)
LINE = RGBColor(0xCB, 0xD5, 0xE1)
SOFT = RGBColor(0xF1, 0xF5, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

HEAD_FONT = "PT Sans"
BODY_FONT = "PT Sans"

W, H = Inches(13.333), Inches(7.5)
PAD = Inches(0.85)
CONTENT_W = W - 2 * PAD


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    return frame


def write(frame, text, size, color=INK, bold=False, font=BODY_FONT,
          align=PP_ALIGN.LEFT, space_after=6, italic=False, first=False):
    """Абзац в текстовом блоке. Первый абзац переиспользуется, иначе добавляется."""
    p = frame.paragraphs[0] if first else frame.add_paragraph()
    p.alignment = align
    p.space_after = Pt(space_after)
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = font
    return p


def rule(slide, top, width=None, color=ACCENT, height=Pt(2.5)):
    """Тонкая линия-акцент под заголовком."""
    from pptx.enum.shapes import MSO_SHAPE
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, PAD, top,
                                   width or Inches(1.2), height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def panel(slide, left, top, width, height, fill=SOFT, border=None):
    from pptx.enum.shapes import MSO_SHAPE
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    shape.adjustments[0] = 0.04
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if border:
        shape.line.color.rgb = border
        shape.line.width = Pt(1)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def heading(slide, title, kicker=None):
    """Шапка слайда: надзаголовок, заголовок, линия."""
    top = Inches(0.55)
    if kicker:
        frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.3))
        write(frame, kicker.upper(), 11, FAINT, bold=True, font=HEAD_FONT, first=True)
        top = Inches(0.9)
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.8))
    write(frame, title, 28, INK, bold=True, font=HEAD_FONT, first=True)
    rule(slide, top + Inches(0.78))
    return top + Inches(1.05)


def bullets(slide, top, items, size=15, gap=8):
    """Пункты с маркером-квадратом. items: строка или (жирное, остальное)."""
    frame = textbox(slide, PAD, top, CONTENT_W, H - top - Inches(0.5))
    for i, item in enumerate(items):
        p = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        p.space_after = Pt(gap)
        marker = p.add_run()
        marker.text = "▪  "
        marker.font.size = Pt(size)
        marker.font.color.rgb = ACCENT
        marker.font.name = BODY_FONT
        head, tail = item if isinstance(item, tuple) else ("", item)
        if head:
            r = p.add_run()
            r.text = head + " "
            r.font.size = Pt(size)
            r.font.bold = True
            r.font.color.rgb = INK
            r.font.name = BODY_FONT
        r = p.add_run()
        r.text = tail
        r.font.size = Pt(size)
        r.font.color.rgb = MUTED
        r.font.name = BODY_FONT
    return frame


def table(slide, top, rows, widths, height=None, size=12, head_size=11):
    """Таблица с заголовком. rows[0] — шапка."""
    n_rows, n_cols = len(rows), len(rows[0])
    total = sum(widths)
    shape = slide.shapes.add_table(n_rows, n_cols, PAD, top, CONTENT_W,
                                   height or Inches(0.32) * n_rows)
    tbl = shape.table
    for c, w in enumerate(widths):
        tbl.columns[c].width = Emu(int(CONTENT_W * w / total))
    for r, row in enumerate(rows):
        tbl.rows[r].height = Inches(0.34)
        for c, value in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = ""
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.08)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(0xEE, 0xF2, 0xF7) if r == 0 else WHITE
            p = cell.text_frame.paragraphs[0]
            run = p.add_run()
            emphatic = value.startswith("*")
            run.text = value.lstrip("*")
            run.font.size = Pt(head_size if r == 0 else size)
            run.font.bold = r == 0 or emphatic
            run.font.color.rgb = INK if (r == 0 or emphatic) else MUTED
            run.font.name = BODY_FONT
    return tbl


def screenshot_slot(slide, left, top, width, height, caption):
    """Рамка под скриншот: вставляет команда."""
    box = panel(slide, left, top, width, height, fill=WHITE, border=LINE)
    frame = box.text_frame
    frame.word_wrap = True
    write(frame, "скриншот", 13, FAINT, bold=True, align=PP_ALIGN.CENTER, first=True)
    write(frame, caption, 11, FAINT, align=PP_ALIGN.CENTER)
    return box


# ------------------------------------------------------------------- слайды

def slide_title(prs):
    slide = blank(prs)
    panel(slide, Inches(0), Inches(0), W, Inches(2.6), fill=INK)
    frame = textbox(slide, PAD, Inches(0.7), CONTENT_W, Inches(1.6))
    write(frame, "СЕРВИС ПРОГНОЗИРОВАНИЯ ИНЦИДЕНТОВ", 15,
          RGBColor(0x94, 0xA3, 0xB8), bold=True, font=HEAD_FONT, first=True)
    write(frame, "Прогноз, которым можно пользоваться", 38, WHITE, bold=True,
          font=HEAD_FONT, space_after=0)

    frame = textbox(slide, PAD, Inches(3.1), CONTENT_W, Inches(2.4))
    write(frame, "Задача №8 «Город» · Лидеры цифровых трансформаций 2026", 17,
          MUTED, first=True)
    write(frame, "Департамент ЖКХ Москвы · АО «Москоллектор»", 15, FAINT)
    write(frame, "Команда «Дискреция творца»", 26, INK, bold=True,
          font=HEAD_FONT, space_after=0)

    frame = textbox(slide, PAD, Inches(5.7), CONTENT_W, Inches(1.2))
    write(frame, "Сервис не выдаёт число. Он выдаёт вердикт с обоснованием "
                 "на языке диспетчера.", 17, ACCENT, italic=True, first=True)
    return slide


def slide_problem(prs):
    slide = blank(prs)
    top = heading(slide, "Прогноз без обоснования нельзя использовать",
                  "проблема")
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.9))
    write(frame, "Диспетчер получил «вероятность отказа 87 %». Он не может "
                 "ни проверить утверждение, ни выбрать, кого направить, "
                 "ни объяснить решение руководству.", 17, INK, first=True)
    write(frame, "Он получил не информацию, а требование доверия.", 17, ACCENT,
          bold=True)

    cards = [("11 485", "каналов в шести системах", "за каждым — живой объект"),
             ("313,5 млн", "событий за 7,5 лет", "просмотреть невозможно"),
             ("0,05 %", "базовая частота отказа", "искать наугад бесполезно")]
    y = top + Inches(1.5)
    w = (CONTENT_W - Inches(0.5)) / 3
    for i, (big, mid, small) in enumerate(cards):
        left = PAD + i * (w + Inches(0.25))
        panel(slide, left, y, w, Inches(1.9))
        frame = textbox(slide, left + Inches(0.25), y + Inches(0.25),
                        w - Inches(0.5), Inches(1.4))
        write(frame, big, 34, ACCENT, bold=True, font=HEAD_FONT, first=True,
              space_after=2)
        write(frame, mid, 14, INK, bold=True, space_after=2)
        write(frame, small, 13, MUTED)

    frame = textbox(slide, PAD, y + Inches(2.2), CONTENT_W, Inches(1.0))
    write(frame, "Поэтому центральная часть решения — не классификатор, "
                 "а слой объяснения.", 19, INK, bold=True, first=True)
    return slide


def slide_card(prs):
    slide = blank(prs)
    top = heading(slide, "Карточка вердикта", "идея решения")

    box = panel(slide, PAD, top, CONTENT_W * 0.62, Inches(4.3), fill=WHITE,
                border=LINE)
    frame = box.text_frame
    frame.margin_left = Inches(0.3)
    frame.margin_top = Inches(0.22)
    frame.margin_right = Inches(0.3)
    write(frame, "Прогноз отказа шлейфа пожарной сигнализации\n"
                 "объект Кси, ПК213 — 87 % в ближайшие 24 часа", 16, INK,
          bold=True, first=True, space_after=10)
    write(frame, "ПОЧЕМУ ТАК РЕШЕНО", 10, FAINT, bold=True, space_after=4)
    for text in ("74 из 75 дымовых датчиков отрезка молчат более 8 часов — вклад +38 %",
                 "канал ДД ПК772 за 7 суток дал 240 переходов «Неисправен» при норме 3 — +22 %",
                 "отрезок непрерывен по пикетам: 57 точек из 59, общий кабель — +15 %"):
        write(frame, "— " + text, 12, MUTED, space_after=3)
    write(frame, "ЭТО НЕ ПОЖАР: сработок дыма за сутки — 0", 12, GOOD,
          bold=True, space_after=4)
    write(frame, "ЧЕГО МЫ НЕ ВИДИМ: тепловых датчиков на объекте нет, "
                 "подтвердить температурой нечем", 12, ACCENT, bold=True,
          space_after=8)
    write(frame, "Что делать: проверить шлейф и питание шкафа ОПС", 12, INK,
          space_after=2)
    write(frame, "Прецедент: 09.12.2025, та же точка", 11, FAINT)

    left = PAD + CONTENT_W * 0.65
    w = CONTENT_W * 0.35
    frame = textbox(slide, left, top, w, Inches(4.3))
    write(frame, "Четыре обязательные части", 17, INK, bold=True,
          font=HEAD_FONT, first=True, space_after=10)
    for head, tail in (("Что произошло.", " Описание, а не код ошибки."),
                       ("Почему так решено.", " Улики с числами, вкладами "
                        "и метками времени до секунды."),
                       ("Это НЕ.", " Версии, которые исключены данными."),
                       ("Чего мы не видим.", " Слепые зоны, из-за которых "
                        "вывод нельзя подтвердить.")):
        p = frame.add_paragraph()
        p.space_after = Pt(9)
        r = p.add_run(); r.text = head
        r.font.size = Pt(14); r.font.bold = True; r.font.color.rgb = ACCENT
        r.font.name = BODY_FONT
        r = p.add_run(); r.text = tail
        r.font.size = Pt(14); r.font.color.rgb = MUTED; r.font.name = BODY_FONT
    write(frame, "Вклад каждой улики считается точно: сумма вкладов сходится "
                 "со скором модели с точностью 4,44·10⁻¹⁶.", 12, FAINT,
          italic=True)
    return slide


def slide_trits(prs):
    slide = blank(prs)
    top = heading(slide, "Троичная логика: «не знаю» — это не «нет»",
                  "что мы сделали иначе")
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.7))
    write(frame, "Состояние канала имеет три значения: +1 отклонение, "
                 "0 неизвестно, −1 норма. Правило заказчика «одиночная "
                 "сработка не событие» записывается одной операцией.", 16,
          INK, first=True)

    rows = [["Операция", "Результат", "Смысл"],
            ["min(+1, −1) = −1", "норма", "сосед спокоен — одиночную тревогу не засчитываем"],
            ["min(+1, +1) = +1", "отклонение", "датчики подтверждают друг друга"],
            ["*min(+1, 0) = 0", "*неизвестно", "*соседа нет — «не знаю», а не «нет»"]]
    table(slide, top + Inches(1.0), rows, [2, 2, 6], size=13)

    frame = textbox(slide, PAD, top + Inches(2.55), CONTENT_W, Inches(0.6))
    write(frame, "Двоичная система обязана округлить «нечем подтвердить» "
                 "до «всё в порядке». Вот сколько это стоит:", 15, INK,
          bold=True, first=True)

    rows = [["Где возникает незнание", "Величина"],
            ["*Локальные события задымления без термоконтроля", "*86,3 %"],
            ["Логические домены из одного канала", "48,5 %"],
            ["Объекты без теплового датчика", "13 из 16"],
            ["Точки без собственного датчика температуры", "3 358 из 3 774"],
            ["Каналы из журнала без паспорта в справочнике", "1 142"]]
    table(slide, top + Inches(3.25), rows, [8, 2], size=12)
    return slide


def slide_data(prs):
    slide = blank(prs)
    top = heading(slide, "Что в данных не так", "данные")
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.5))
    write(frame, "313 546 016 событий · 2019–2026 · 11 485 каналов · "
                 "16 объектов · 19 типов датчиков", 16, INK, bold=True, first=True)
    bullets(slide, top + Inches(0.75), [
        ("Журнал событийный, а не телеметрический.", "Заполнено 13,6 % "
         "возможных канало-суток. Отсутствие записи не означает норму."),
        ("1 142 канала без паспорта.", "9,44 млн событий от каналов, которых "
         "нет в справочнике. Ответ 19 считает вопрос закрытым — проверка 27.09 "
         "показывает, что нет."),
        ("42 % всех тревог — один период 2021 года.", "Внедрение новой версии "
         "системы мониторинга. Подтверждено заказчиком, период исключён."),
        ("86 % отказных суток — работа бригады, а не отказ.", "После "
         "маскирования массовых сработок доля выходных среди позитивов выросла "
         "с 5,7 % до 24 %: таргет перестал быть графиком обходов."),
        ("1 036 каналов-тёзок.", "Дубли одного физического датчика: "
         "синхронность ≥ 95 % у 386 групп из 412. В обучении остаётся один."),
    ], size=14, gap=11)
    return slide


def slide_method(prs):
    slide = blank(prs)
    top = heading(slide, "Метод", "как это работает")
    w = (CONTENT_W - Inches(0.6)) / 3
    blocks = [
        ("Семантический контракт",
         "Смысл задаёт пара «тип датчика + значение», а не значение. "
         "211 пар, 100 % объёма событий, каждая отображена в трит.",
         "Соединение вместо 211 условий: витрины собираются за 11 секунд"),
        ("Признаки-утверждения",
         "20 признаков, каждый несёт шаблон фразы. Объяснение собирается "
         "подстановкой, а не генерируется.",
         "Расхождение между расчётом и текстом технически невозможно"),
        ("Личная норма канала",
         "Отношения считаются к собственной норме за 90 суток и по месяцам — "
         "сезонность встроена.",
         "Относительная улика требует абсолютной опоры: ×12,9 при одном "
         "событии — артефакт деления"),
    ]
    for i, (title, body, note) in enumerate(blocks):
        left = PAD + i * (w + Inches(0.3))
        panel(slide, left, top, w, Inches(3.5))
        frame = textbox(slide, left + Inches(0.25), top + Inches(0.25),
                        w - Inches(0.5), Inches(3.0))
        write(frame, title, 18, INK, bold=True, font=HEAD_FONT, first=True,
              space_after=8)
        write(frame, body, 13, MUTED, space_after=10)
        write(frame, note, 12, ACCENT, italic=True)

    frame = textbox(slide, PAD, top + Inches(3.8), CONTENT_W, Inches(1.2))
    write(frame, "Аналитический контур: DuckDB и Parquet — 313 млн событий "
                 "превращаются в витрины на ноутбуке с 16 ГБ ОЗУ. "
                 "Оперативный контур: PostgreSQL — вердикты, решения, заявки, аудит.",
          15, INK, first=True)
    return slide


def slide_block1_metrics(prs):
    slide = blank(prs)
    top = heading(slide, "Блок 1. Прогноз отказов оборудования",
                  "результат · оценивается отдельно")
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.8))
    write(frame, "Единственная модель проекта с настоящей разметкой: "
                 "целевое событие выведено из семантики значений, а не "
                 "имитировано. 18 056 743 канало-суток, 9 353 отказа, "
                 "базовая частота 0,0518 %.", 16, INK, first=True)

    rows = [["Схема валидации", "Значение"],
            ["Обучение", "2022-01-01 … 2024-12-31"],
            ["*Отложенная проверка", "*2025-01-01 … 2026-06-30, без прореживания"],
            ["Разбиение", "строго временное, группировка по «объект — месяц»"],
            ["Почему не случайное", "соседние сутки канала почти идентичны — модель запомнила бы канал"]]
    table(slide, top + Inches(1.0), rows, [3.5, 6.5], size=12)

    rows = [["Метрика", "Значение", "Как читать"],
            ["ROC-AUC", "0,807", "при доле позитивов 0,05 % не показателен"],
            ["PR-AUC", "0,0109", "главная метрика для редкого события"],
            ["*Precision@100", "*0,16", "*в 301 раз плотнее случайного выбора"]]
    table(slide, top + Inches(2.9), rows, [2.5, 2, 5.5], size=12)

    frame = textbox(slide, PAD, top + Inches(4.35), CONTENT_W, Inches(0.7))
    write(frame, "Эталонного тестового периода организаторы не предусмотрели "
                 "(ответ 2) — схему валидации мы определяем и защищаем сами.",
          13, FAINT, italic=True, first=True)
    return slide


def slide_recall(prs):
    slide = blank(prs)
    top = heading(slide, "Почему Recall 0,5 недостижим — и это не про модель",
                  "честность вместо подгонки")
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.6))
    write(frame, "ТЗ называет Precision 0,70 и Recall 0,50. Организаторы "
                 "разрешили снизить их с обоснованием. Вот обоснование — "
                 "измеренное, а не оценённое.", 16, INK, first=True)

    rows = [["Целевой Recall", "Предупреждений за 18 месяцев", "Precision",
             "Проверок в сутки"],
            ["*0,10 — рабочий режим", "*7 880", "*3,1 %", "*14"],
            ["0,20", "65 860", "0,73 %", "120"],
            ["0,30", "136 955", "0,53 %", "250"],
            ["*0,50 — цель ТЗ", "*390 105", "*0,31 %", "*713"]]
    table(slide, top + Inches(0.85), rows, [3.2, 3.3, 1.8, 1.7], size=13)

    y = top + Inches(2.75)
    w = (CONTENT_W - Inches(0.3)) / 2
    panel(slide, PAD, y, w, Inches(1.9), fill=SOFT)
    frame = textbox(slide, PAD + Inches(0.25), y + Inches(0.25), w - Inches(0.5),
                    Inches(1.4))
    write(frame, "713 проверок в сутки", 24, ACCENT, bold=True, font=HEAD_FONT,
          first=True, space_after=4)
    write(frame, "чтобы поймать половину отказов. Из них отказом окажутся две. "
                 "Это не режим работы, а способ потерять доверие к сервису.",
          14, MUTED)

    panel(slide, PAD + w + Inches(0.3), y, w, Inches(1.9), fill=SOFT)
    frame = textbox(slide, PAD + w + Inches(0.55), y + Inches(0.25),
                    w - Inches(0.5), Inches(1.4))
    write(frame, "14 проверок в сутки", 24, GOOD, bold=True, font=HEAD_FONT,
          first=True, space_after=4)
    write(frame, "закрывают десятую часть всех отказов сети с плотностью "
                 "в 58 раз выше случайного обхода. Это нагрузка на смену.",
          14, MUTED)

    frame = textbox(slide, PAD, y + Inches(2.1), CONTENT_W, Inches(0.6))
    write(frame, "Поэтому в интерфейсе нет порога «тревога / не тревога» — "
                 "есть приоритетная очередь.", 16, INK, bold=True, first=True)
    return slide


def slide_horizon(prs):
    slide = blank(prs)
    top = heading(slide, "Целевая точность достигается — на другом горизонте",
                  "два режима продукта")
    rows = [["Горизонт", "Позитивов", "Базовая частота", "Precision@50",
             "Цель 0,70"],
            ["24 часа", "2 401", "0,053 %", "0,18", "нет, потолок 0,18"],
            ["3 суток", "6 647", "0,147 %", "0,08", "нет"],
            ["7 суток", "14 645", "0,324 %", "0,12", "нет"],
            ["*14 суток", "*27 906", "*0,617 %", "*0,52", "*да, 0,73"]]
    table(slide, top, rows, [2, 2, 2.4, 2, 2.6], size=13)

    frame = textbox(slide, PAD, top + Inches(1.9), CONTENT_W, Inches(1.0))
    write(frame, "Отказ оборудования вызревает неделями: медиана вызревания "
                 "признаков — 33 суток. Чем шире окно, тем плотнее сигнал.",
          17, INK, first=True)

    y = top + Inches(2.9)
    w = (CONTENT_W - Inches(0.3)) / 2
    for i, (title, body) in enumerate([
        ("Аварийный поток · 24 часа",
         "Приоритетная очередь на смену. Полезен там, где важна скорость: "
         "каскадный отказ питающей линии, ночное проникновение, рост "
         "водопритока."),
        ("Сервисный поток · 14 суток",
         "Планирование ремонтов с точностью 0,73. Заявка с обоснованием, "
         "сроком и исполнителем — до того, как оборудование встанет.")]):
        left = PAD + i * (w + Inches(0.3))
        panel(slide, left, y, w, Inches(2.0))
        frame = textbox(slide, left + Inches(0.25), y + Inches(0.25),
                        w - Inches(0.5), Inches(1.5))
        write(frame, title, 19, INK, bold=True, font=HEAD_FONT, first=True,
              space_after=8)
        write(frame, body, 14, MUTED)

    frame = textbox(slide, PAD, y + Inches(2.15), CONTENT_W, Inches(0.5))
    write(frame, "Это совпадает с тем, как заказчик сам делит работу: "
                 "аварийный поток и сервисный поток. Горизонт — настройка, "
                 "а не константа в коде.", 13, FAINT, italic=True, first=True)
    return slide


def slide_block2(prs):
    slide = blank(prs)
    top = heading(slide, "Блок 2. Прогноз инцидентов",
                  "результат · оценивается отдельно")
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.7))
    write(frame, "Разметки инцидентов в данных нет — это подтвердили "
                 "организаторы. Кандидатов на реальное задымление за 7,5 лет — "
                 "порядка десяти. Классификатор на таком материале выучил бы "
                 "шум, поэтому здесь детекторы, а не обучение с учителем.",
          15, INK, first=True)

    rows = [["Сценарий", "Метод", "Почему так", "Масштаб"],
            ["Подтопление", "аномалия частоты пусков насоса",
             "рост водопритока виден раньше датчика затопления",
             "462 255 пусков, 180 насосов"],
            ["Задымление", "многофакторный детектор с отсевом прогонов ТО",
             "прогон сигнализации — не дым",
             "50 564 кандидата, 36 476 — ТО"],
            ["Проникновение", "правила: охрана, ночь, мультисенсорность",
             "3 303 сработки за 12 часов — неисправный шлейф, не человек",
             "10 из 47 охранных каналов залипли"],
            ["*Отказ датчика", "*обучение с учителем",
             "*единственная настоящая разметка", "*9 353 отказа"]]
    table(slide, top + Inches(0.9), rows, [2, 3, 4, 2.6], size=11)

    frame = textbox(slide, PAD, top + Inches(3.5), CONTENT_W, Inches(1.2))
    write(frame, "Сервис не утверждает, что предсказывает пожар.", 18, ACCENT,
          bold=True, first=True, space_after=6)
    write(frame, "Он утверждает, что обнаруживает признаки развивающегося "
                 "события раньше, чем оно станет очевидным, — и честно "
                 "показывает, когда подтвердить его нечем. В 86,3 % локальных "
                 "событий задымления теплового датчика в точке просто нет.",
          15, MUTED)
    return slide


def slide_cascade(prs):
    slide = blank(prs)
    top = heading(slide, "Один каскад — одно событие, а не 75 отказов",
                  "единица события")
    frame = textbox(slide, PAD, top, CONTENT_W, Inches(0.9))
    write(frame, "Вопрос «отказ шлейфа из 75 каналов — это одно событие или "
                 "семьдесят пять» организаторы оставили без ответа. "
                 "Решение принято нами и обосновано физикой: заказчик описал "
                 "питающую линию 48 В, отказ которой обесточивает десятки "
                 "точек одновременно.", 16, INK, first=True)

    y = top + Inches(1.3)
    panel(slide, PAD, y, CONTENT_W, Inches(1.7), fill=SOFT)
    frame = textbox(slide, PAD + Inches(0.35), y + Inches(0.25),
                    CONTENT_W - Inches(0.7), Inches(1.2))
    write(frame, "объект Кси: 39 признаков на 22 точках за 17 секунд", 24, ACCENT,
          bold=True, font=HEAD_FONT, first=True, space_after=6)
    write(frame, "При суточной точности метк это 39 независимых отказов "
                 "и 39 карточек. При секундной — один отказ питающей линии "
                 "и одна заявка на ремонт.", 15, MUTED)

    bullets(slide, y + Inches(2.1), [
        ("Окно 120 секунд, не менее 4 точек и 2 пикетов.", "Порог выведен "
         "из распределения, а не назначен."),
        ("Каскад поднимает приоритет заявки до аварийного.", "Ждать планового "
         "обхода при обесточенном луче нельзя."),
        ("Все 469 метк в наборе вердиктов — посекундные.", "Это то, что делает "
         "каскады видимыми вообще."),
    ], size=15, gap=10)
    return slide


def slide_product(prs):
    slide = blank(prs)
    top = heading(slide, "Рабочее место диспетчера", "продукт")
    w = (CONTENT_W - Inches(0.4)) / 2
    screenshot_slot(slide, PAD, top, w, Inches(2.5),
                    "очередь на проверку: 162 вердикта, сортировка по риску")
    screenshot_slot(slide, PAD + w + Inches(0.4), top, w, Inches(2.5),
                    "карточка вердикта с уликами, вкладами и таймлайном")
    screenshot_slot(slide, PAD, top + Inches(2.7), w, Inches(2.5),
                    "интерактивная карта с выделением проблемных участков")
    screenshot_slot(slide, PAD + w + Inches(0.4), top + Inches(2.7), w, Inches(2.5),
                    "линейная схема коллектора по пикетам")
    return slide


def slide_flow(prs):
    slide = blank(prs)
    top = heading(slide, "От вердикта к заявке", "замкнутый цикл")
    steps = [("Вердикт", "карточка с уликами и метками времени"),
             ("Мультикарточка", "карточки объекта собраны вместе: "
              "люди едут на объект, а не на канал"),
             ("Квитирование", "пока человек не отработал, событие "
              "не исчезает из журнала"),
             ("Решение", "причина из справочника заказчика, "
              "ложные срабатывания отделены"),
             ("Заявка", "обоснование переносится из карточки дословно, "
              "срок — из вызревания признаков")]
    w = (CONTENT_W - Inches(0.8)) / 5
    for i, (title, body) in enumerate(steps):
        left = PAD + i * (w + Inches(0.2))
        panel(slide, left, top, w, Inches(2.4), fill=SOFT)
        frame = textbox(slide, left + Inches(0.18), top + Inches(0.22),
                        w - Inches(0.36), Inches(2.0))
        write(frame, str(i + 1), 15, ACCENT, bold=True, font=HEAD_FONT,
              first=True, space_after=2)
        write(frame, title, 16, INK, bold=True, font=HEAD_FONT, space_after=6)
        write(frame, body, 12, MUTED)

    bullets(slide, top + Inches(2.8), [
        ("Обоснование заявки не пишется руками.", "Оно переносится из карточки "
         "с теми же числами и метками времени, иначе заявка со временем "
         "разойдётся с данными и потеряет доказательную силу."),
        ("Срок и приоритет выводятся из вызревания.", "То, что копилось месяц, "
         "не обязано чиниться завтра. Срок сдвигается перед длинными "
         "выходными: бригад в эти дни меньше."),
        ("Пары «вердикт — решение» — это разметка, которой нет ни у нас, "
         "ни у заказчика.", "Журнал прогнозов накапливает её с первого дня "
         "эксплуатации."),
    ], size=14, gap=9)
    return slide


def slide_compliance(prs):
    slide = blank(prs)
    top = heading(slide, "Соответствие ТЗ", "что сделано и чего нет")
    rows = [["Требование ТЗ", "Состояние"],
            ["Прогноз с вероятностью и горизонтом не менее 24 часов", "да, горизонт настраивается"],
            ["Четыре сценария: отказ, подтопление, задымление, проникновение", "да, разными методами"],
            ["Дашборд, интерактивная карта, линейная схема, журнал прогнозов", "да, семь страниц"],
            ["Квитирование, фиксация решения, справочник причин", "да"],
            ["Черновики заявок на ремонт", "да"],
            ["Рекомендации по ТО, настраиваемые параметры", "да"],
            ["REST API, PostgreSQL, развёртывание одной командой", "да"],
            ["Роли, двухфакторный вход, журнал аудита", "да"],
            ["Отчёты XLSX", "да · PDF не реализован"],
            ["Уведомления в реальном времени", "частично: квитирование есть, push нет"],
            ["Интеграции со СМВУ и журналом ОДС", "эмуляция — систем в контуре задачи нет"],
            ["Реестр оборудования", "суррогат из поведения каналов — реестра не существует"]]
    table(slide, top, rows, [7.5, 2.5], size=11, head_size=11)

    frame = textbox(slide, PAD, top + Inches(4.45), CONTENT_W, Inches(1.0))
    write(frame, "Сплошной проход по разделам 4–19: 108 проверяемых требований. "
                 "Выполнено 69, семь работают на эмулированных или суррогатных "
                 "источниках, 18 частично, 11 не реализовано, три — ссылки пакета.",
          15, INK, bold=True, first=True, space_after=4)
    write(frame, "У каждого пункта в записке стоит оговорка: на чём основано "
                 "утверждение или почему требование не выполнено. Все одиннадцать "
                 "невыполненных — это отсутствие внешнего источника, обвязка "
                 "периметра или сознательное сокращение при недельном сроке.",
          13, MUTED)
    return slide


def slide_deploy(prs):
    slide = blank(prs)
    top = heading(slide, "Развёртывание и воспроизводимость", "сдача")
    panel(slide, PAD, top, CONTENT_W, Inches(1.2), fill=INK)
    frame = textbox(slide, PAD + Inches(0.35), top + Inches(0.25),
                    CONTENT_W - Inches(0.7), Inches(0.8))
    write(frame, "docker compose up", 30, WHITE, bold=True, font="PT Mono",
          first=True, space_after=2)
    write(frame, "http://localhost:8080 · одноразовый код показывается "
                 "на странице входа", 14, RGBColor(0x94, 0xA3, 0xB8))

    bullets(slide, top + Inches(1.6), [
        ("Ни датасета, ни витрин в образе нет.", "Вердикты поставляются файлом "
         "инициализации, поэтому стенд поднимается за секунды на любой машине."),
        ("Полный пересчёт — пять команд.", "Витрины из 313 млн событий, "
         "обучение, кривая горизонтов, вердикты. Проходит на ноутбуке "
         "с 16 ГБ ОЗУ: лимиты памяти заданы в коде."),
        ("Код открытый, без обфускации.", "5 646 строк Python, классов нет "
         "сознательно: только функции и модули, читается сверху вниз."),
        ("Числа презентации собраны скриптом из моделей.", "Слайд нельзя "
         "случайно оставить со старой метрикой после переобучения."),
    ], size=15, gap=11)
    return slide


def slide_closing(prs):
    slide = blank(prs)
    panel(slide, Inches(0), Inches(0), W, H, fill=INK)
    frame = textbox(slide, PAD, Inches(1.1), CONTENT_W, Inches(1.2))
    write(frame, "ЧТО ОТЛИЧАЕТ РЕШЕНИЕ", 14, RGBColor(0x94, 0xA3, 0xB8),
          bold=True, font=HEAD_FONT, first=True)

    items = [("Вердикт, а не число.",
              "Каждая улика — факт из данных с меткой времени и точным вкладом "
              "в решение. Объяснение собирается подстановкой, а не пишется."),
             ("Троичная логика.",
              "«Не знаю» сохраняется и проговаривается. Сервис, который "
              "в слепой зоне скажет «риск низкий», обманет диспетчера."),
             ("Измеренная честность.",
              "Recall 0,5 стоит 713 проверок в сутки — мы это посчитали "
              "и показали, вместо того чтобы подогнать метрику под ТЗ.")]
    y = Inches(2.1)
    for i, (head, tail) in enumerate(items):
        frame = textbox(slide, PAD, y + i * Inches(1.35), CONTENT_W, Inches(1.2))
        write(frame, head, 26, WHITE, bold=True, font=HEAD_FONT, first=True,
              space_after=4)
        write(frame, tail, 16, RGBColor(0xCB, 0xD5, 0xE1))

    frame = textbox(slide, PAD, Inches(6.3), CONTENT_W, Inches(0.8))
    write(frame, "Команда «Дискреция творца» · задача №8 «Город»", 16,
          RGBColor(0x94, 0xA3, 0xB8), first=True)
    return slide


def main():
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    for builder in (slide_title, slide_problem, slide_card, slide_trits,
                    slide_data, slide_method, slide_block1_metrics,
                    slide_recall, slide_horizon, slide_block2, slide_cascade,
                    slide_product, slide_flow, slide_compliance,
                    slide_deploy, slide_closing):
        builder(prs)

    prs.save(TARGET)
    print(f"собрано: {TARGET}")
    print(f"слайдов: {len(prs.slides.__iter__.__self__._sldIdLst)}")


if __name__ == "__main__":
    main()
