"""Переносит словарь из core/glossary.py в сопроводительный документ.

Зачем отдельный шаг. Словарь показывается на странице «Точность» и должен
быть в PDF, который читают без доступа к стенду. Держать два текста
вручную нельзя: они разойдутся на первой же правке, и расхождение
обнаружит эксперт, а не мы.

Поэтому источник один — `core/glossary.py`, а документ собирается из него
этим скриптом. Правка словаря в markdown бессмысленна: следующий запуск
её перепишет. Тест `test_glossary_document_is_in_sync` падает, если
документ отстал.

Запуск:  python -m scripts.15_sync_glossary
"""

import os

from core import config, glossary, plural

TARGET = os.path.join(config.ROOT, "docs", "17-повышение-точности.md")
BEGIN = "<!-- СЛОВАРЬ: НАЧАЛО, собирается scripts/15_sync_glossary -->"
END = "<!-- СЛОВАРЬ: КОНЕЦ -->"


def section():
    return "\n".join([BEGIN, "", glossary.as_markdown(), END])


def main():
    with open(TARGET, encoding="utf-8") as fh:
        src = fh.read()

    block = section()
    if BEGIN in src and END in src:
        start = src.index(BEGIN)
        finish = src.index(END) + len(END)
        src = src[:start] + block + src[finish:]
    else:
        raise SystemExit(
            f"в {TARGET} нет меток словаря. Добавьте строки:\n{BEGIN}\n{END}")

    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(src)
    print("словарь перенесён: "
          + plural.count(len(glossary.terms()), "термин", "термина",
                         "терминов")
          + " в " + plural.count(len(glossary.GLOSSARY), "группе", "группах",
                                 "группах"))


if __name__ == "__main__":
    main()
