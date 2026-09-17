"""Объяснение вердикта.

Вклад каждого признака считается ТОЧНО, без внешних библиотек: объект проводится
по дереву, и на каждом узле изменение вероятности приписывается тому признаку,
по которому шло ветвление. Сумма вкладов плюс базовая вероятность в точности
равна предсказанию — это проверяется тестом, а не декларируется.

Почему не важность признаков из коробки: она глобальная и отвечает на вопрос
«что важно для модели вообще». Диспетчеру нужен ответ на другой вопрос —
«почему именно этот канал и именно сегодня».

Карточка улик собирается из шаблонов фраз (core/features.py) подстановкой чисел.
Текст детерминирован: вердикт не может сказать того, чего нет в данных.
"""

import numpy as np

from core import trits
from core.features import FEATURES, FEATURE_NAMES


def tree_contributions(tree, x):
    """Вклады признаков одного дерева для одного объекта."""
    t = tree.tree_
    node = 0
    # доля положительного класса в узле
    def prob(n):
        v = t.value[n][0]
        total = v.sum()
        return v[1] / total if total else 0.0

    bias = prob(0)
    contrib = np.zeros(len(FEATURE_NAMES))
    while t.children_left[node] != -1:
        feat = t.feature[node]
        nxt = t.children_left[node] if x[feat] <= t.threshold[node] else t.children_right[node]
        contrib[feat] += prob(nxt) - prob(node)
        node = nxt
    return bias, contrib


def forest_contributions(forest, x):
    """Вклады по всему лесу: усреднение по деревьям.

    Возвращает (базовая вероятность, вектор вкладов). По построению
    bias + sum(contrib) равно forest.predict_proba(x)[1].
    """
    biases = np.zeros(len(forest.estimators_))
    contribs = np.zeros((len(forest.estimators_), len(FEATURE_NAMES)))
    for i, tree in enumerate(forest.estimators_):
        biases[i], contribs[i] = tree_contributions(tree, x)
    return biases.mean(), contribs.mean(axis=0)


def check_additivity(forest, X, tol=1e-9):
    """Проверка, что декомпозиция точная, а не приблизительная.

    Если сумма вкладов расходится с предсказанием, объяснение недостоверно
    и показывать его диспетчеру нельзя.
    """
    worst = 0.0
    for row in X:
        bias, contrib = forest_contributions(forest, row)
        pred = forest.predict_proba(row.reshape(1, -1))[0, 1]
        worst = max(worst, abs(bias + contrib.sum() - pred))
    return worst <= tol, worst


def top_evidence(forest, x, values, baselines=None, k=4):
    """Топ-k улик: признаки, сильнее всего толкнувшие вердикт вверх.

    Возвращает список словарей с готовой фразой и вкладом в процентных пунктах.
    """
    bias, contrib = forest_contributions(forest, x)
    order = np.argsort(-contrib)
    out = []
    for idx in order[:k]:
        name = FEATURE_NAMES[idx]
        if contrib[idx] <= 0:
            break
        spec = FEATURES[name]
        base = (baselines or {}).get(name)
        value = values[name]
        out.append({
            "feature": name,
            "group": spec["group"],
            "value": float(value),
            "contribution": float(contrib[idx]),
            "phrase": spec["phrase"].format(v=value, base=base if base is not None else 0),
        })
    return bias, out


def counterfactual(forest, x, feature_index, normal_value):
    """Насколько упадёт риск, если признак вернётся к норме.

    Превращает прогноз в конкретное действие: диспетчер видит не только «почему»,
    но и «что изменится, если это починить».
    """
    before = forest.predict_proba(x.reshape(1, -1))[0, 1]
    y = x.copy()
    y[feature_index] = normal_value
    after = forest.predict_proba(y.reshape(1, -1))[0, 1]
    return float(before), float(after)


def observability_note(domain_size, domain_silent_share, has_thermal=None):
    """Строка «чего мы не видим» — обязательная часть вердикта.

    В 1 823 доменах из 3 759 всего один канал: подтверждать нечем. Вердикт обязан
    сказать это прямо, иначе диспетчер примет отсутствие подтверждения за его
    отсутствие по существу.
    """
    notes = []
    if domain_size is not None and domain_size <= 1:
        notes.append("в этой точке только один канал — подтвердить показания нечем")
    elif domain_silent_share is not None and domain_silent_share >= 0.5:
        notes.append(
            f"молчит {domain_silent_share:.0%} соседних каналов точки — "
            "картина неполная"
        )
    if has_thermal is False:
        notes.append("тепловых датчиков в этой точке нет, подтвердить температурой нельзя")
    return notes


def build_card(verdict_type, location, probability, bias, evidence,
               negatives=None, observability=None, counterfactual_text=None):
    """Карточка вердикта целиком — то, что видит диспетчер.

    Четыре обязательные части: что, почему, что это НЕ, и чего мы не видим.
    Последняя часть не косметика: она отличает «всё в порядке» от «я не знаю».
    """
    return {
        "type": verdict_type,
        "location": location,
        "probability": probability,
        "baseline": bias,
        "evidence": evidence,
        "not_this": negatives or [],
        "blind_spots": observability or [],
        "counterfactual": counterfactual_text,
    }


def render_card(card):
    """Текстовое представление карточки — для консоли, отчёта и видео."""
    lines = [
        f"{card['location']} — {card['type']}, {card['probability']:.0%} в ближайшие 24 ч",
        "",
        "Почему так решено:",
    ]
    for i, e in enumerate(card["evidence"], 1):
        lines.append(f"  {i}. {e['phrase']}")
        lines.append(f"     вклад {e['contribution']:+.1%}")
    if card["not_this"]:
        lines.append("")
        for n in card["not_this"]:
            lines.append(f"Это НЕ {n}")
    if card["blind_spots"]:
        lines.append("")
        lines.append("Чего мы не видим:")
        for b in card["blind_spots"]:
            lines.append(f"  • {b}")
    if card["counterfactual"]:
        lines.append("")
        lines.append(card["counterfactual"])
    return "\n".join(lines)
