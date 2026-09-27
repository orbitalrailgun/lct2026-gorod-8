"""Сборка сопроводительной документации в PDF.

ТЗ, раздел 19: сдаётся ссылка на сопроводительную документацию в .docx или
.pdf. Исходник записки ведётся в markdown, потому что он читается в diff
и правится без редактора; PDF собирается из него одной командой, поэтому
расхождение между исходником и поставляемым файлом невозможно.

Вёрстка через WeasyPrint: он умеет колонтитулы, нумерацию страниц
и оглавление со ссылками на номера страниц (CSS target-counter), чего
не даёт печать из браузера.

Шрифты — семейство Paratype (PT Serif, PT Sans, PT Mono): они рисовались
под кириллицу, и текст на русском в них выглядит так, как должен.

Установка инструментов (в состав сервиса не входят):

    pip install -r requirements-docs.txt

Запуск:

    python -m scripts.07_build_docs
"""

import os
import re

import markdown
from weasyprint import HTML

from core import config

SOURCE = os.path.join(config.ROOT, "docs", "13-пояснительная-записка.md")
TARGET = os.path.join(config.ROOT, "docs", "пояснительная-записка.pdf")

CSS = """
@page {
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
    @bottom-center {
        content: counter(page);
        font-family: "PT Sans", sans-serif;
        font-size: 8pt;
        color: #888;
    }
    @top-right {
        content: "Дискреция творца · задача №8 «Город»";
        font-family: "PT Sans", sans-serif;
        font-size: 7.5pt;
        color: #aaa;
    }
}
/* На титуле колонтитулов нет. */
@page :first { @bottom-center { content: "" } @top-right { content: "" } }

html { font-family: "PT Serif", Georgia, serif; font-size: 10pt; line-height: 1.45;
       color: #1a1a1a; hyphens: auto; }
body { margin: 0 }

h1, h2, h3, h4 { font-family: "PT Sans", "Helvetica Neue", sans-serif;
                 color: #0f172a; line-height: 1.2; }
h1 { font-size: 19pt; margin: 0 0 14pt 0; padding-bottom: 5pt;
     border-bottom: 2pt solid #0f172a; break-before: page; }
h2 { font-size: 13pt; margin: 16pt 0 6pt 0; }
h3 { font-size: 11pt; margin: 12pt 0 4pt 0; color: #334155; }
p { margin: 0 0 7pt 0; text-align: justify; }

/* ---- титул ---- */
.cover { break-after: page; padding-top: 55mm; text-align: center; }
.cover h1 { border: none; break-before: avoid; font-size: 16pt; color: #475569;
            font-weight: normal; letter-spacing: 0.06em; text-transform: uppercase;
            margin-bottom: 26pt; }
.cover h2 { font-size: 25pt; line-height: 1.25; margin: 0 0 30pt 0; color: #0f172a; }
.cover p  { text-align: center; margin: 0 0 4pt 0; color: #334155; }
.cover .team { font-family: "PT Sans", sans-serif; font-size: 14pt; margin-top: 26pt;
               color: #0f172a; }
.cover hr { width: 45mm; margin: 26pt auto; border: none; border-top: 1pt solid #cbd5e1; }
.cover .date { margin-top: 40mm; font-size: 9pt; color: #64748b; }

/* ---- оглавление ---- */
.toc-title { font-family: "PT Sans", sans-serif; font-size: 19pt; color: #0f172a;
             border-bottom: 2pt solid #0f172a; padding-bottom: 5pt; margin-bottom: 12pt; }
.toc ul { list-style: none; padding-left: 0; margin: 0; }
.toc > ul > li { margin: 4pt 0; font-family: "PT Sans", sans-serif; font-size: 10pt; }
.toc ul ul { padding-left: 7mm; }
.toc ul ul li { font-size: 8.5pt; color: #475569; margin: 1pt 0; }
.toc a { text-decoration: none; color: inherit; }
.toc a::after { content: " " leader(dotted) " " target-counter(attr(href), page);
                color: #94a3b8; }

/* ---- таблицы ---- */
table { width: 100%; border-collapse: collapse; margin: 7pt 0 10pt 0;
        font-family: "PT Sans", sans-serif; font-size: 8pt; }
thead { display: table-header-group; }
th { background: #eef2f7; text-align: left; font-weight: bold; color: #0f172a; }
th, td { border: 0.5pt solid #cbd5e1; padding: 2.5pt 4pt; vertical-align: top; }
tr { break-inside: avoid; }

/* ---- код и схемы ---- */
pre { font-family: "PT Mono", Menlo, monospace; font-size: 7pt; line-height: 1.3;
      background: #f8fafc; border-left: 2pt solid #94a3b8; padding: 5pt 7pt;
      white-space: pre; overflow: hidden; break-inside: avoid; }
code { font-family: "PT Mono", Menlo, monospace; font-size: 8.5pt;
       background: #f1f5f9; padding: 0 2pt; }
pre code { background: none; font-size: 7pt; padding: 0; }

/* ---- цитаты заказчика и организаторов ---- */
blockquote { margin: 7pt 0 9pt 0; padding: 4pt 0 4pt 8pt;
             border-left: 2.5pt solid #94a3b8; color: #334155; font-style: italic; }
blockquote p { text-align: left; margin-bottom: 3pt; }

hr { border: none; border-top: 0.5pt solid #e2e8f0; margin: 10pt 0; }
strong { color: #0f172a; }
ul, ol { margin: 0 0 7pt 0; padding-left: 6mm; }
li { margin-bottom: 2pt; }
em { color: #475569; }
"""

COVER = """
<div class="cover">
  <h1>Сопроводительная документация</h1>
  <h2>Сервис прогнозирования инцидентов<br>в инженерных коллекторах</h2>
  <hr>
  <p>Задача №8 «Город»</p>
  <p>Конкурс «Лидеры цифровых трансформаций 2026»</p>
  <hr>
  <p>Постановщик: Департамент жилищно-коммунального хозяйства города Москвы</p>
  <p>Эксплуатант: АО «Москоллектор»</p>
  <p class="team">Команда «Дискреция творца»</p>
  <p class="date">Москва, сентябрь 2026</p>
</div>
"""


def split_source(text):
    """Отделяет титульный блок от тела: всё до маркера [TOC] печатается отдельно."""
    parts = text.split("[TOC]", 1)
    if len(parts) == 1:
        return "", text
    return parts[0], parts[1]


def build_html(body_md):
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc",
                                       "attr_list", "sane_lists"],
                           extension_configs={"toc": {"title": ""}})
    # Оглавление собирается по заголовкам тела, титул в него не попадает.
    body_html = md.convert("[TOC]\n\n" + body_md)
    body_html = body_html.replace('<div class="toc">',
                                  '<div class="toc-title">Содержание</div>'
                                  '<div class="toc">', 1)
    return (f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<title>Сопроводительная документация — Дискреция творца</title>"
            f"<style>{CSS}</style></head><body>{COVER}{body_html}</body></html>")


def main():
    with open(SOURCE, encoding="utf-8") as fh:
        text = fh.read()
    _, body = split_source(text)
    # Горизонтальные разделители между разделами в PDF не нужны: разрыв страницы
    # перед каждым разделом делает то же самое, но чище.
    body = re.sub(r"\n---\n", "\n", body)

    html = build_html(body)
    HTML(string=html, base_url=config.ROOT).write_pdf(TARGET)
    size = os.path.getsize(TARGET) / 1024
    print(f"собрано: {TARGET}")
    print(f"размер: {size:.0f} КБ")


if __name__ == "__main__":
    main()
