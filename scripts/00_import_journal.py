"""Шаг 0: перевод журнала событий из CSV в Parquet.

Исходные файлы — восемь годовых выгрузок общим объёмом около 16 ГБ.
Работать с ними напрямую можно, но каждый последующий шаг перечитывал бы
текст и заново разбирал даты; в Parquet те же данные занимают 1,9 ГБ
и читаются на порядок быстрее.

Колонки переименовываются в латиницу один раз здесь, чтобы дальше
по конвейеру не таскать кириллицу в именах полей. Значение датчика
сохраняется дважды: как есть строкой и как число там, где приведение
удалось. Строка нужна семантическому контракту — смысл задаётся парой
«тип датчика + значение», и «Норма» не превращается в NULL; число нужно
телеметрии.

Память: файлы обрабатываются по одному, лимит DuckDB берётся из
core/config.py. На машине с 16 ГБ проходит без обращения к диску.

Запуск:  python -m scripts.00_import_journal
"""

import glob
import os
import re
import time

from core import config, etl

COLUMNS = """
    ид_события          AS event_id,
    ид_канала_данных    AS channel_id,
    дата                AS d,
    время               AS t,
    тревожное           AS is_alarm,
    значение_датчика    AS raw_value,
    TRY_CAST(значение_датчика AS DOUBLE) AS num_value
"""


def main():
    sources = sorted(glob.glob(os.path.join(config.DATASET_DIR, "ext-journal-*.csv")))
    if not sources:
        raise SystemExit(
            f"в {config.DATASET_DIR} нет файлов ext-journal-*.csv.\n"
            "Распакуйте датасет: unzip dataset.zip -d dataset")

    os.makedirs(config.PARQUET_DIR, exist_ok=True)
    con = etl.connect()
    total_rows = 0
    t_all = time.time()

    for source in sources:
        year = re.search(r"(\d{4})", os.path.basename(source)).group(1)
        target = os.path.join(config.PARQUET_DIR, f"j{year}.parquet")
        t0 = time.time()

        con.execute(f"""
            COPY (SELECT {COLUMNS} FROM read_csv('{source}', header=true))
            TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)

        rows = con.execute(
            f"SELECT count(*) FROM read_parquet('{target}')").fetchone()[0]
        total_rows += rows
        size = os.path.getsize(target) / 1048576
        print(f"  {year}:  {rows:>12,} строк  →  {size:>6.0f} МБ  "
              f"({time.time() - t0:.0f} с)".replace(",", " "), flush=True)

    print(f"\nвсего {total_rows:,} событий за {time.time() - t_all:.0f} с"
          .replace(",", " "))
    print(f"готово: {config.PARQUET_DIR}")


if __name__ == "__main__":
    main()
