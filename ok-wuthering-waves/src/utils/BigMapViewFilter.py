import math
from collections import deque
from statistics import median


class BigMapViewFilter:
    WINDOW = 7
    HOLD_PIXELS = 6.0
    CONSISTENT_PIXELS = 3.0

    def __init__(self):
        self.reset()

    def reset(self):
        self.context = None
        self.samples = deque(maxlen=self.WINDOW)
        self.center = None
        self.game_scale = None

    def update(self, center, game_scale, map_id, viewport, interacting=False):
        center = tuple(float(value) for value in center)
        game_scale = float(game_scale)
        if len(center) != 2 or not all(math.isfinite(value) for value in center):
            raise ValueError('地图中心坐标无效')
        if not math.isfinite(game_scale) or game_scale <= 0:
            raise ValueError('地图比例无效')
        context = (map_id, tuple(viewport))
        if context != self.context:
            self.reset()
            self.context = context
        sample = (center, game_scale)
        if self.center is None or interacting:
            self.samples.clear()
            self.center, self.game_scale = sample
        self.samples.append(sample)
        if interacting or len(self.samples) < self.WINDOW:
            return self.center, self.game_scale

        candidate = (
            (median(value[0][0] for value in self.samples),
             median(value[0][1] for value in self.samples)),
            median(value[1] for value in self.samples),
        )
        radius = math.hypot(*viewport) / 2

        def error(left, right):
            # 使用画面边缘的最大投影变化判断中心与比例是否真正改变。
            return (math.dist(left[0], right[0]) / right[1]
                    + radius * abs(left[1] / right[1] - 1))

        current = (self.center, self.game_scale)
        if error(candidate, current) > self.HOLD_PIXELS:
            recent = list(self.samples)[-3:]
            if all(error(value, candidate) <= self.CONSISTENT_PIXELS for value in recent):
                self.center, self.game_scale = candidate
        return self.center, self.game_scale
