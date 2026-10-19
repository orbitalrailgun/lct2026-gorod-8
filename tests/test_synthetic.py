"""Тесты синтетического объекта.

Главное, что здесь проверяется, — честность эксперимента. Если физика
начнёт зависеть от оснащения, вывод «поставьте эти датчики, и точность
вырастет» превратится в пересказ наших собственных настроек. Поэтому
инварианты проверяются не на глаз, а тестом.
"""

import pytest

from core import synthetic


@pytest.fixture(scope="module")
def calibration():
    return synthetic.load_calibration()


@pytest.fixture(scope="module")
def physics(calibration):
    return synthetic.build_physics(calibration, days=200, pickets=60, seed=7)


def test_physics_is_reproducible(calibration):
    """Одно зерно — одна и та же физика. Иначе прогоны несравнимы."""
    a = synthetic.build_physics(calibration, days=200, pickets=60, seed=7)
    b = synthetic.build_physics(calibration, days=200, pickets=60, seed=7)
    assert synthetic.ground_truth(a) == synthetic.ground_truth(b)


def test_physics_does_not_depend_on_equipment(physics):
    """Несущий инвариант: оснащение не меняет того, что произошло.

    Наблюдение зависит от датчиков, события — нет. Вода прибывает и там,
    где датчика затопления никто не поставил.
    """
    truth = synthetic.ground_truth(physics)

    poor = synthetic.configuration("бедная", {"Датчик дыма": 1})
    rich = synthetic.configuration(
        "богатая", {"Датчик дыма": 1, "Тепловой датчик": 1,
                    "Датчик температуры": 2, "Датчик затопления": 2},
        polling={"Датчик дыма": 30})

    synthetic.observe(physics, poor)
    synthetic.observe(physics, rich)

    assert synthetic.ground_truth(physics) == truth, \
        "наблюдение изменило физику — эксперимент стал бы подгонкой"


def test_equipment_changes_what_is_seen(physics):
    """А вот журнал обязан зависеть от оснащения, иначе мерить нечего."""
    poor = synthetic.configuration("бедная", {"Датчик дыма": 4})
    rich = synthetic.configuration("богатая",
                                   {"Датчик дыма": 1, "Тепловой датчик": 2})
    _, poor_rows = synthetic.observe(physics, poor)
    _, rich_rows = synthetic.observe(physics, rich)
    assert len(rich_rows) > len(poor_rows)


def test_core_sensors_present_in_every_configuration(physics):
    """Метрики отказов считаются по базовому набору, и он обязан быть всюду."""
    for sensors in ({"Датчик дыма": 1},
                    {"Датчик дыма": 1, "Тепловой датчик": 2},
                    {"Датчик дыма": 1, "Датчик движения": 3, "КД Люк": 5}):
        cfg = synthetic.configuration("проба", sensors)
        channels = synthetic.channels_of(cfg, physics["pickets"])
        kinds = {c["sensor_type"] for c in channels}
        for core in synthetic.CORE_SENSORS:
            assert core in kinds


def test_core_device_count_is_stable(physics):
    """Популяция для метрики одна и та же: иначе сравнение нечестно."""
    counts = set()
    for sensors in ({"Датчик дыма": 1},
                    {"Датчик дыма": 1, "Тепловой датчик": 2},
                    {"Датчик дыма": 1, "Газовый датчик": 8, "КД Люк": 5}):
        cfg = synthetic.configuration("проба", sensors)
        channels = synthetic.channels_of(cfg, physics["pickets"])
        counts.add(sum(1 for c in channels
                       if c["sensor_type"] in synthetic.CORE_SENSORS))
    assert len(counts) == 1, f"базовый набор поплыл между конфигурациями: {counts}"


def test_values_belong_to_the_contract(physics):
    """Значение не из словаря типа контракт отнесёт к мусору, и запись пропадёт."""
    from core import contract
    table = contract.load_contract()
    cfg = synthetic.configuration(
        "полная", {"Датчик дыма": 1, "Тепловой датчик": 2,
                   "Датчик температуры": 4, "Состояние насоса": 10,
                   "Датчик движения": 3, "КД Люк": 5})
    _, rows = synthetic.observe(physics, cfg)
    channels = {c["channel_id"]: c["sensor_type"]
                for c in synthetic.channels_of(cfg, physics["pickets"])}
    unknown = set()
    for row in rows:
        if row["num_value"] is not None:
            continue                      # числа контракт разбирает отдельно
        key = (channels[row["channel_id"]], row["raw_value"])
        if key not in table:
            unknown.add(key)
    assert not unknown, f"значения вне контракта: {sorted(unknown)[:5]}"


def test_without_self_diagnostics_there_are_no_fault_messages(physics):
    """Прибор без самодиагностики о неисправности молчит — в этом вся разница."""
    quiet = synthetic.configuration("без самодиагностики", {"Датчик дыма": 1},
                                    self_diagnostics=False)
    loud = synthetic.configuration("с самодиагностикой", {"Датчик дыма": 1},
                                   self_diagnostics=True)
    _, quiet_rows = synthetic.observe(physics, quiet)
    _, loud_rows = synthetic.observe(physics, loud)
    faults = {"Неисправен"}
    assert not [r for r in quiet_rows if r["raw_value"] in faults]
    assert [r for r in loud_rows if r["raw_value"] in faults]


def test_experiment_object_is_isolated_by_identifier():
    """Синтетика живёт в своём диапазоне идентификаторов и не пересекается
    с реальными каналами даже случайно."""
    assert synthetic.CHANNEL_ID_BASE > 500_000
    assert synthetic.EXPERIMENT_OBJECT_ID > 90_000


def test_maintenance_sweeps_are_filtered_by_service_criterion(physics):
    """Обход техника не должен считаться подтверждённой тревогой.

    Во время регламентного прогона срабатывают сразу оба типа датчиков
    в одной точке, и правило «два независимых типа подтвердили друг друга»
    принимает это за идеальное подтверждение. Измерено: без фильтра доля
    подтверждённых получается 2 % вместо 100 %. Фильтр берётся из сервиса
    (core/fire.py, критерий массовости), а не выдумывается заново, поэтому
    тест проверяет, что критерий сервиса на синтетике вообще работает —
    то есть находит прогоны и отбрасывает их.
    """
    import duckdb
    from core import etl, fire

    cfg = synthetic.configuration("проба",
                                  {"Датчик дыма": 1, "Тепловой датчик": 2})
    channels, journal = synthetic.observe(physics, cfg)

    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=2")
    try:
        synthetic.stage(con, channels, journal)
        etl.build_fact_channel_day(con)
        fire.build_smoke_events(con)
        total, sweeps = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_sweep) FROM smoke_event"
        ).fetchone()
        assert total > 0, "дымовых сработок нет — проверять нечего"
        assert sweeps > 0, (
            "критерий массовости сервиса не нашёл ни одного прогона ТО; "
            "значит генератор их не порождает и метрика переоценит точность")
        assert sweeps < total, "прогонами объявлены все сработки"
    finally:
        con.close()
