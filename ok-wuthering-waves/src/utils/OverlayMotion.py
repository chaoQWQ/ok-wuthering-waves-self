import math
from dataclasses import replace

from src.utils.overlay_types import Rect


def viewport_motion_pixels(origin, scale, center, game_scale, viewport):
    if not all(math.isfinite(value) for value in (*origin, scale, *center, game_scale)):
        raise ValueError('地图投影参数需要有限数值')
    if scale <= 0 or game_scale <= 0:
        raise ValueError('地图比例必须大于零')
    return (math.dist(origin, center) / game_scale +
            math.hypot(*viewport) / 2 * abs(scale / game_scale - 1))


def transform_overlay(items, hitboxes, marker, layers, origin, scale, center, game_scale, viewport,
                      pixel_center=None):
    if not all(math.isfinite(value) for value in (*origin, scale, *center, game_scale)):
        raise ValueError('地图投影参数需要有限数值')
    if scale <= 0 or game_scale <= 0:
        raise ValueError('地图比例必须大于零')
    cx, cy = pixel_center or (viewport[0] / 2, viewport[1] / 2)
    ratio = scale / game_scale

    def project(x, y):
        return (round((x - cx) * ratio + cx + (origin[0] - center[0]) / game_scale),
                round((y - cy) * ratio + cy + (origin[1] - center[1]) / game_scale))

    projected_items = [(*project(item[0], item[1]), *item[2:]) for item in items]
    projected_boxes = []
    for rect, z in hitboxes:
        x, y = project(rect.left + rect.width / 2, rect.top + rect.height / 2)
        left, top = x - rect.width // 2, y - rect.height // 2
        projected_boxes.append((Rect(left, top, left + rect.width, top + rect.height), z))
    projected_layers = tuple(replace(layer, points=tuple(project(*point) for point in layer.points))
                             for layer in layers)
    return projected_items, projected_boxes, project(*marker) if marker is not None else None, projected_layers
