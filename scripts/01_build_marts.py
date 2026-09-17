"""День 1: сборка витрин из сырого журнала.

Запуск:  .venv/bin/python -m scripts.01_build_marts
"""

import time

from core import etl


def main():
    t0 = time.time()
    con = etl.connect()
    etl.register_sources(con)
    print("источники подключены, справочник каналов прочитан корректно", flush=True)

    n = etl.build_dim_value_class(con)
    print(f"dim_value_class:   {n:>12,} пар значений", flush=True)

    n = etl.build_dim_channel(con)
    print(f"dim_channel:       {n:>12,} каналов", flush=True)
    etl.export(con, "dim_channel")

    t = time.time()
    n = etl.build_fact_channel_day(con)
    print(f"fact_channel_day:  {n:>12,} строк  ({time.time()-t:.0f} с)", flush=True)
    etl.export(con, "fact_channel_day")

    n = etl.build_fact_object_day(con)
    print(f"fact_object_day:   {n:>12,} строк", flush=True)
    etl.export(con, "fact_object_day")

    print(f"\nготово за {time.time()-t0:.0f} с", flush=True)


if __name__ == "__main__":
    main()
