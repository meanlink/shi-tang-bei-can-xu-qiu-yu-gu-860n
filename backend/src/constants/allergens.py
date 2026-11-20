"""过敏原、忌口与配餐规则常量。

标签版本与菜谱绑定；批次在建立时锁定一个过敏原版本，下发时再核对一次，
防止“替料更换菜谱后仍按旧标签下发”。
"""

DEFAULT_ALLERGEN_VERSION = '2026.11'


ALLERGEN_LABELS = {
    'gluten': '麸质',
    'milk': '乳制品',
    'egg': '蛋',
    'soy': '大豆',
    'peanut': '花生',
    'tree_nut': '坚果',
    'fish': '鱼',
    'shellfish': '甲壳类',
    'sesame': '芝麻',
    'celery': '芹菜',
}


def label_text(code):
    return ALLERGEN_LABELS.get(code, code)


def label_list(codes):
    return [label_text(c) for c in codes or []]


def catalog():
    return {
        'default_version': DEFAULT_ALLERGEN_VERSION,
        'allergens': [{'code': code, 'label': label} for code, label in ALLERGEN_LABELS.items()],
        'label_statuses': [{'value': 'known', 'label': '标签明确'}, {'value': 'unknown', 'label': '标签不明'}],
        'batch_statuses': [
            {'value': 'pending', 'label': '待配'},
            {'value': 'allocating', 'label': '配餐中'},
            {'value': 'short', 'label': '待补'},
            {'value': 'ready', 'label': '配齐待下发'},
            {'value': 'dispatched', 'label': '已下发'},
        ],
        'meal_statuses': [
            {'value': 'pending', 'label': '待分配'},
            {'value': 'allocated', 'label': '已配齐'},
            {'value': 'held', 'label': '挂起待重配'},
            {'value': 'dispatched', 'label': '已下发'},
            {'value': 'produced', 'label': '已出餐'},
        ],
    }
