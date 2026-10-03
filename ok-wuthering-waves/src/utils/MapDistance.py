import math


WORLD_UNITS_PER_METER = 100.0


def format_distance_meters(distance):
    """将地图距离换算为米，按整数四舍五入显示。"""
    value = float(distance)
    if not math.isfinite(value) or value < 0:
        raise ValueError('地图距离必须是有限的非负数值')
    return f'{math.floor(value / WORLD_UNITS_PER_METER + 0.5)} m'
