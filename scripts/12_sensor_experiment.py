"""Эксперимент: какой набор датчиков нужен, чтобы точность выросла.

Одна и та же физика коллектора прогоняется через разные конфигурации
оснащения. Модель при этом не переобучается — берётся та же, что работает
на реальных данных. Значит, любая разница в метриках есть следствие
оснащения, а не наших настроек.

Синтетический объект проходит настоящий конвейер без единой поблажки:
журнал событий того же вида, тот же семантический контракт, тот же
построитель признаков, та же модель. Поблажек нет нигде, кроме одного
места — целевая переменная берётся из физики, а не из наблюдений.
Иначе сравнение было бы невозможно: конфигурация без самодиагностики
не дала бы ни одного положительного примера, и метрика потеряла бы смысл.

Метрики считаются только по базовому набору приборов, одинаковому во всех
конфигурациях. Добавив датчиков, мы добавили бы и новых кандидатов
на поломку, и сравнение перестало бы быть честным.

Запуск:  python -m scripts.12_sensor_experiment
"""

import datetime as dt
import json
import os
import pickle

import duckdb
import numpy as np

from core import config, etl, features, fire, model, synthetic
from core.features import FEATURE_NAMES

TARGET = os.path.join(config.MODEL_DIR, "sensor_experiment.json")
SEED_FILE = os.path.join(config.ROOT, "deploy", "seed_experiment.json")

DAYS = 540
PICKETS = 400
# Несколько независимых прогонов с разными зёрнами. При сорока отказах
# precision@k скачет сильнее, чем меняется от оснащения, и разницу между
# конфигурациями не увидеть. Усреднение по зёрнам убирает этот шум.
SEEDS = (42, 43, 44)

# Конфигурации от текущего состояния сети до полного оснащения.
# Отличаются ровно тем, что организаторы и спрашивают: набор датчиков,
# шаг установки, периодичность опроса, пороги, самодиагностика.
CONFIGURATIONS = [
    dict(name="как сейчас",
         sensors={"Датчик дыма": 1},
         polling={}, self_diagnostics=True, line_monitoring=False,
         controller_size=12,
         note="типовая точка сети: только дымовые датчики, событийный режим"),
    dict(name="без самодиагностики",
         sensors={"Датчик дыма": 1},
         polling={}, self_diagnostics=False, line_monitoring=False,
         controller_size=12,
         note="приборы типа «сухой контакт»: о своей неисправности молчат"),
    dict(name="+ контроль линии",
         sensors={"Датчик дыма": 1},
         polling={}, self_diagnostics=True, line_monitoring=True,
         controller_size=12,
         note="добавлен контроль связи до прибора"),
    dict(name="+ регулярный опрос",
         sensors={"Датчик дыма": 1},
         polling={"Датчик дыма": 60}, self_diagnostics=True,
         line_monitoring=True, controller_size=12,
         note="опрос раз в час: молчание начинает что-то значить"),
    dict(name="+ тепловой контроль",
         sensors={"Датчик дыма": 1, "Тепловой датчик": 2},
         polling={"Датчик дыма": 60}, self_diagnostics=True,
         line_monitoring=True, controller_size=12,
         note="тепловой датчик через пикет: появляется подтверждение дыма"),
    dict(name="+ температура и затопление",
         sensors={"Датчик дыма": 1, "Тепловой датчик": 2,
                  "Датчик температуры": 4, "Датчик затопления": 6,
                  "Состояние насоса": 10},
         polling={"Датчик дыма": 60, "Датчик температуры": 30},
         self_diagnostics=True, line_monitoring=True, controller_size=12,
         note="добавлены температура, затопление и контроль насосов"),
    dict(name="+ чувствительные приборы",
         sensors={"Датчик дыма": 1, "Тепловой датчик": 2,
                  "Датчик температуры": 4, "Датчик затопления": 6,
                  "Состояние насоса": 10},
         polling={"Датчик дыма": 60, "Датчик температуры": 30},
         self_diagnostics=True, line_monitoring=True, controller_size=12,
         # Единственное отличие от «температуры и затопления» — порог
         # срабатывания, то есть чувствительность прибора. Так ось
         # «диапазон и точность измерения» отделена от набора датчиков:
         # приборы те же, шаг тот же, опрос тот же.
         thresholds={"heat": 6.0, "flood": 1.2, "temperature": 1.5},
         note="те же приборы, но вдвое чувствительнее: порог теплового "
              "датчика 6 °C вместо 12, затопления 1,2 вместо 2,5"),
    dict(name="+ подтверждение в каждой точке",
         sensors={"Датчик дыма": 1, "Тепловой датчик": 1,
                  "Датчик температуры": 4, "Датчик затопления": 6,
                  "Состояние насоса": 10},
         polling={"Датчик дыма": 60, "Датчик температуры": 30},
         self_diagnostics=True, line_monitoring=True, controller_size=12,
         # Узкое место полноты подтверждённых тревог — шаг подтверждающего
         # прибора: при шаге 2 часть очагов остаётся без второго типа
         # рядом. Здесь тепловой датчик стоит в каждой точке, и это
         # предельное по подтверждению оснащение.
         note="тепловой датчик в каждой точке, а не через одну: предельное "
              "подтверждение дыма"),
    dict(name="плацебо: датчик движения",
         sensors={"Датчик дыма": 1, "Датчик движения": 1},
         polling={}, self_diagnostics=True, line_monitoring=False,
         controller_size=12,
         note="отрицательный контроль: датчик движения ничего не знает "
              "о здоровье дымового прибора, но удваивает число каналов "
              "в точке и на контроллере"),
    dict(name="полное оснащение",
         sensors={"Датчик дыма": 1, "Тепловой датчик": 2,
                  "Датчик температуры": 4, "Датчик затопления": 6,
                  "Состояние насоса": 10, "Датчик движения": 3,
                  "КД Люк": 5, "Газовый датчик": 8},
         polling={"Датчик дыма": 30, "Датчик температуры": 15,
                  "Датчик движения": 30},
         self_diagnostics=True, line_monitoring=True, controller_size=8,
         note="всё перечисленное плюс охранный контур и газоанализ"),
]


def load_model():
    with open(os.path.join(config.MODEL_DIR, "m1_failure.pkl"), "rb") as fh:
        return pickle.load(fh)["models"]["forest"]


def failure_metrics(con, forest):
    """Точность прогноза отказа на базовом наборе приборов."""
    cols = ", ".join(FEATURE_NAMES)
    df = con.execute(f"SELECT y, {cols} FROM features ORDER BY channel_id, d").df()
    y = df["y"].values
    X = df[FEATURE_NAMES].astype("float32")
    X = X.replace([np.inf, -np.inf], np.nan).fillna(model.MISSING)
    scores = forest.predict_proba(X.values)[:, 1]

    positives = int(y.sum())
    order = np.argsort(-scores)
    result = {"строк": int(len(y)), "отказов": positives,
              "базовая_частота": float(y.mean())}
    for k in (10, 20, 50):
        taken = y[order[:k]]
        result[f"precision@{k}"] = float(taken.mean())
        result[f"recall@{k}"] = float(taken.sum() / positives) if positives else 0.0
    # Полнота при рабочей нагрузке: одна проверка в сутки на объект.
    k_day = min(len(y), int(round(DAYS)))
    taken = y[order[:k_day]]
    result["precision@сутки"] = float(taken.mean())
    result["recall@сутки"] = float(taken.sum() / positives) if positives else 0.0
    return result


def incident_metrics(con, physics, channels):
    """Обнаружение и подтверждаемость инцидентов.

    Здесь синтетика даёт то, чего на реальных данных нет вовсе: разметку
    инцидентов. Но считается при этом не Precision сервиса: на реальных
    данных её не существует, потому что разметки нет. Считается доля
    подтверждённых при заданной физике — то есть насколько наше правило
    согласуется с нашей же объявленной физикой. Это инструмент вывода
    требований к оснащению, а не измерение качества.

    Регламентные прогоны отбрасываются критерием самого сервиса
    (fire.build_smoke_events, поля is_sweep и base_share). Это не
    косметика: измерено на пробе из 200 суток, что без фильтра обход
    техника считается подтверждённой тревогой и доля подтверждённых
    получается 2 % вместо 100 %, потому что во время прогона срабатывают
    сразу оба типа датчиков. На реальных данных у одной базы
    зафиксировано 27 суток, когда разом срабатывали все 96 дымовых
    каналов по всем 45 пикетам.

    Фильтр стоит денег: вместе с прогонами теряются и настоящие события,
    случившиеся в те же сутки. Поэтому число отброшенных суток
    возвращается в метрике, а не умалчивается.
    """
    start = physics["start"]
    by_picket = {}
    for channel in channels:
        by_picket.setdefault(channel["picket"], []).append(channel["sensor_type"])

    triggers = con.execute("""
        SELECT c.picket, f.d, c.sensor_type, f.n_trigger
        FROM fact_channel_day f JOIN dim_channel c USING (channel_id)
        WHERE f.n_trigger > 0
    """).df()
    # Сравнивать дату с меткой времени pandas напрямую нельзя: получается
    # тихий ноль совпадений вместо ошибки. Приводим явно.
    triggers["d"] = triggers["d"].astype("datetime64[ns]").dt.date

    # Сутки регламентного прогона по критерию сервиса. Берётся не своё
    # правило, а то же, что работает на реальной сети.
    #
    # Критерий считается по дымовым каналам, а применяется ко всем трём
    # сценариям, и это осознанное упрощение: обходчик проверяет приборы
    # подряд, а не только дымовые. В сервисе у насосов и охранного контура
    # свои механизмы отсева массовых событий (pumps.NETWORK_HOT_MIN,
    # intrusion.STUCK_ARMED_SHARE), и здесь они не воспроизводятся.
    # Упрощение консервативное: оно только убирает тревоги, а цена —
    # пропущенные настоящие события в те же сутки — возвращается
    # в метрике полями «суток_прогона_ТО» и «сработок_отброшено_как_ТО».
    fire.build_smoke_events(con)
    sweeps = con.execute(
        "SELECT DISTINCT d FROM smoke_event WHERE is_sweep").df()
    if len(sweeps):
        sweeps["d"] = sweeps["d"].astype("datetime64[ns]").dt.date
        sweep_days = set(sweeps["d"])
    else:
        sweep_days = set()
    before = len(triggers)
    triggers = triggers[~triggers["d"].isin(sweep_days)]
    dropped = before - len(triggers)

    def day_of(item):
        return start + dt.timedelta(days=item["day"])

    out = {}
    for key, kinds in (("задымление", ("Датчик дыма", "Тепловой датчик")),
                       ("подтопление", ("Состояние насоса", "Датчик затопления")),
                       ("проникновение", ("КД Люк", "КД Дверь", "Датчик движения"))):
        source = {"задымление": "fires", "подтопление": "floods",
                  "проникновение": "intrusions"}[key]
        real = physics[source]
        detected = confirmed = 0
        for item in real:
            day = day_of(item)
            near = triggers[(triggers.d == day)
                            & (triggers.picket.between(item["picket"] - 2,
                                                       item["picket"] + 2))
                            & (triggers.sensor_type.isin(kinds))]
            if len(near):
                detected += 1
                if near.sensor_type.nunique() >= 2:
                    confirmed += 1
        # Ложные тревоги: сработки тех же типов там, где события не было.
        real_days = {(day_of(i), i["picket"]) for i in real}
        alarms = triggers[triggers.sensor_type.isin(kinds)]
        false_alarms = sum(
            1 for _, row in alarms.iterrows()
            if not any(abs(row.picket - p) <= 2 and row.d == day
                       for day, p in real_days))
        # Ложные тревоги, прошедшие подтверждение вторым типом. Именно
        # на этом и стоит весь смысл дооснащения: одиночная сработка
        # ничего не доказывает, а две независимые — доказывают.
        confirmed_false = 0
        for (day, picket), group in alarms.groupby(["d", "picket"]):
            if group.sensor_type.nunique() < 2:
                continue
            if not any(abs(picket - p) <= 2 and day == d for d, p in real_days):
                confirmed_false += 1

        # Цена фильтра: настоящие события, случившиеся в отброшенные сутки.
        # Это обязательно предъявлять: критерий массовости считается
        # по доле каналов базы, а число каналов на контроллер — одна
        # из варьируемых характеристик. То есть агрессивность фильтра
        # зависит от конфигурации, и без этого числа разница между
        # конфигурациями частично объяснялась бы поведением фильтра,
        # а не способностью обнаруживать.
        lost = sum(1 for i in real if day_of(i) in sweep_days)

        out[key] = {
            "суток_прогона_ТО": len(sweep_days),
            "сработок_отброшено_как_ТО": int(dropped),
            "событий_в_отброшенных_сутках": int(lost),
            "событий": len(real), "обнаружено": detected,
            "подтверждено_вторым_типом": confirmed,
            "ложных_тревог": int(false_alarms),
            "ложных_подтверждённых": int(confirmed_false),
            "recall": detected / len(real) if real else 0.0,
            "precision": detected / (detected + false_alarms)
                         if (detected + false_alarms) else 0.0,
            # Режим с требованием подтверждения: тревога поднимается, только
            # если сработали два независимых типа датчиков.
            "recall_подтверждённых": confirmed / len(real) if real else 0.0,
            "precision_подтверждённых": (confirmed / (confirmed + confirmed_false)
                                          if (confirmed + confirmed_false) else 0.0),
            "подтверждаемость": confirmed / detected if detected else 0.0,
            "типы_в_наличии": sorted({t for types in by_picket.values()
                                      for t in types if t in kinds}),
        }
    return out


def run(config_spec, physics, forest):
    cfg = synthetic.configuration(
        config_spec["name"], config_spec["sensors"],
        polling=config_spec.get("polling"),
        self_diagnostics=config_spec.get("self_diagnostics", True),
        line_monitoring=config_spec.get("line_monitoring", False),
        controller_size=config_spec.get("controller_size", 12),
        thresholds=config_spec.get("thresholds"))

    channels, journal = synthetic.observe(physics, cfg)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{config.DUCKDB_MEMORY}'")
    con.execute(f"SET threads={config.DUCKDB_THREADS}")

    synthetic.stage(con, channels, journal)
    etl.build_fact_channel_day(con)
    positives, rows = synthetic.build_labels(con, physics, channels)
    features.build_features(con)

    result = {
        "конфигурация": config_spec["name"],
        "пояснение": config_spec["note"],
        "каналов": len(channels),
        "записей_журнала": len(journal),
        "датчиков": config_spec["sensors"],
        "опрос_минут": config_spec.get("polling", {}),
        "самодиагностика": config_spec.get("self_diagnostics", True),
        "контроль_линии": config_spec.get("line_monitoring", False),
        "каналов_на_контроллер": config_spec.get("controller_size", 12),
        "пороги_срабатывания": cfg["thresholds"],
        "отказы": failure_metrics(con, forest),
        "инциденты": incident_metrics(con, physics, channels),
    }
    con.close()
    return result


def average(runs):
    """Усреднение по зёрнам: числа складываются, вложенные словари — рекурсивно."""
    first = runs[0]
    out = {}
    for key, value in first.items():
        values = [r[key] for r in runs]
        if isinstance(value, dict):
            out[key] = average(values)
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            out[key] = value
        else:
            out[key] = sum(values) / len(values)
    return out


def main():
    # Пересчитать одну конфигурацию и влить её в готовый файл:
    #   ONLY_CONFIG="плацебо: датчик движения" python -m scripts.12_sensor_experiment
    # Нужно, чтобы добавление контроля не требовало полного прогона сетки.
    only = os.environ.get("ONLY_CONFIG") or ""
    calibration = synthetic.load_calibration()
    forest = load_model()

    worlds = [synthetic.build_physics(calibration, days=DAYS, pickets=PICKETS,
                                      seed=seed) for seed in SEEDS]
    truths = [synthetic.ground_truth(w) for w in worlds]
    totals = {k: sum(len(t[k]) for t in truths) for k in truths[0]}
    print(f"физика: {len(worlds[0]['devices'])} приборов базового набора "
          f"× {len(SEEDS)} прогона", flush=True)
    print(f"истина суммарно: {totals}\n", flush=True)

    results = []
    planned = [s for s in CONFIGURATIONS if not only or s["name"] == only]
    if only and not planned:
        raise SystemExit(f"конфигурация «{only}» в сетке не найдена")
    for spec in planned:
        print(f"  {spec['name']}…", flush=True)
        runs = [run(spec, world, forest) for world in worlds]
        merged = average(runs)
        merged["прогонов"] = len(SEEDS)
        results.append(merged)
        fire = merged["инциденты"]["задымление"]
        print(f"      отказы: P@50 {merged['отказы']['precision@50']:.0%}, "
              f"полнота за смену {merged['отказы']['recall@сутки']:.0%} | "
              f"дым без подтверждения: P {fire['precision']:.1%} | "
              f"с подтверждением: P {fire['precision_подтверждённых']:.0%} "
              f"R {fire['recall_подтверждённых']:.0%}", flush=True)

    payload = {
        "физика": {"суток": DAYS, "пикетов": PICKETS, "зёрна": list(SEEDS),
                   "истина_суммарно": totals},
        "конфигурации": results,
    }
    if only and os.path.exists(TARGET):
        # Порядок конфигураций в файле задаёт порядок на странице,
        # поэтому пересчитанная точка встаёт на своё место в сетке.
        with open(TARGET, encoding="utf-8") as fh:
            before = json.load(fh)
        merged_rows = {r["конфигурация"]: r for r in before["конфигурации"]}
        merged_rows.update({r["конфигурация"]: r for r in results})
        order = [s["name"] for s in CONFIGURATIONS]
        payload["конфигурации"] = [merged_rows[n] for n in order
                                   if n in merged_rows]
        payload["физика"] = before["физика"]
    with open(TARGET, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    with open(SEED_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"\nсохранено: {TARGET}")


if __name__ == "__main__":
    main()
