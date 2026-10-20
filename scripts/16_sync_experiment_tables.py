"""Переносит таблицу конфигураций из расчёта в сопроводительные документы.

Зачем отдельный шаг, а не правка руками. Таблица есть на странице
«Точность» и в двух документах комплекта, и однажды подпись столбца
«дым: доля подтв.» означала на экране одну величину — долю настоящих
среди всех сработок, 2,2 %, — а в записке другую: долю настоящих среди
подтверждённых, 0 или 100 %. Один и тот же столбец показывал разные
числа в двух документах, и заметил это не автор.

Поэтому подписи берутся из `core.glossary.GRID_LABELS`, числа — из
`models/sensor_experiment.json`, то есть из того же файла, который читает
интерфейс. Правка таблицы в markdown бессмысленна: следующий запуск
её перепишет.

Запуск:  python -m scripts.16_sync_experiment_tables
"""

import json
import os

from core import cards, config, glossary, plural

EXPERIMENT = os.path.join(config.MODEL_DIR, "sensor_experiment.json")
BEGIN = "<!-- ТАБЛИЦА: НАЧАЛО, собирается scripts/16_sync_experiment_tables -->"
END = "<!-- ТАБЛИЦА: КОНЕЦ -->"

TARGETS = (
    os.path.join(config.ROOT, "docs", "17-повышение-точности.md"),
    os.path.join(config.ROOT, "docs", "13-пояснительная-записка.md"),
)

# В записке таблица короче: там она иллюстрирует вывод, а не служит
# рабочим материалом. Столбцы те же самые, просто не все.
SHORT = ("конфигурация", "каналов", "отказы_полнота",
         "дым_подтв_точность", "дым_подтв_полнота")


def pct(x):
    return f"{x * 100:.0f} %".replace(".", ",")


def rows_of(data, keys):
    out = []
    for r in data["конфигурации"]:
        cell = {
            "конфигурация": r["конфигурация"],
            "каналов": f"{r['каналов']:.0f}",
            "отказы_полнота": pct(r["отказы"]["recall@сутки"]),
            "отказы_точность": pct(r["отказы"]["precision@50"]),
            "дым_сработка": pct(cards.incident_value(r, "задымление",
                                                     "recall")),
            "дым_точность": f"{cards.incident_value(r, 'задымление', 'precision') * 100:.1f} %".replace(".", ","),
            "дым_подтв_точность": pct(cards.incident_value(
                r, "задымление", "precision_подтверждённых")),
            "дым_подтв_полнота": pct(cards.incident_value(
                r, "задымление", "recall_подтверждённых")),
            "фильтр": (f"{cards.incident_value(r, 'задымление', 'событий_в_отброшенных_сутках', 0):.0f}"
                       f" из {cards.incident_value(r, 'задымление', 'событий', 0):.0f}"),
            "пояснение": r["пояснение"],
        }
        out.append("| " + " | ".join(cell[k] for k in keys) + " |")
    return out


def table(data, keys, note=True):
    head = "| " + " | ".join(glossary.GRID_LABELS[k].capitalize()
                             for k in keys) + " |"
    sep = "|" + "---|" * len(keys)
    body = rows_of(data, keys)
    out = [BEGIN, "", head, sep] + body
    if note:
        out += ["", glossary.GRID_NOTE]
    out += ["", END]
    return "\n".join(out)


def main():
    with open(EXPERIMENT, encoding="utf-8") as fh:
        data = json.load(fh)

    for path in TARGETS:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        if BEGIN not in src or END not in src:
            raise SystemExit(f"в {path} нет меток таблицы. Добавьте строки:\n"
                             f"{BEGIN}\n{END}")
        short = path.endswith("13-пояснительная-записка.md")
        block = table(data, SHORT if short else list(glossary.GRID_LABELS),
                      note=not short)
        start = src.index(BEGIN)
        finish = src.index(END) + len(END)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src[:start] + block + src[finish:])
        print(f"  {os.path.basename(path)}: "
              + plural.count(len(data["конфигурации"]), "строка", "строки",
                             "строк")
              + ", " + plural.count(
                  len(SHORT) if short else len(glossary.GRID_LABELS),
                  "столбец", "столбца", "столбцов"))

    print("таблица перенесена из " + os.path.basename(EXPERIMENT))


if __name__ == "__main__":
    main()
