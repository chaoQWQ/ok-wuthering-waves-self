import json
import re
import unittest
from pathlib import Path

from src.utils.BigMapViewFilter import BigMapViewFilter
from src.utils.ViewStateEstimator import ViewStateEstimator


ROOT = Path(__file__).resolve().parents[1]


class TestBigMapViewStability(unittest.TestCase):
    def test_stationary_samples_preserve_projection(self):
        filter_state = BigMapViewFilter()
        first = filter_state.update((-83079, 32801), 54.8, 8, (1920, 1080))
        for index in range(100):
            center = (-83079 + index % 3 * 15, 32801 - index % 2 * 20)
            scale = 54.8 + (index % 3 - 1) * 0.03
            self.assertEqual(filter_state.update(center, scale, 8, (1920, 1080)), first)

    def test_input_and_context_changes_update_immediately(self):
        filter_state = BigMapViewFilter()
        filter_state.update((0, 0), 55, 8, (1920, 1080))
        self.assertEqual(filter_state.update((2000, 1000), 48, 8, (1920, 1080), True),
                         ((2000.0, 1000.0), 48.0))
        self.assertEqual(filter_state.update((9000, 5000), 30, 912, (1920, 1080)),
                         ((9000.0, 5000.0), 30.0))
        self.assertEqual(filter_state.update((8000, 5000), 40, 912, (2560, 1440)),
                         ((8000.0, 5000.0), 40.0))

    def test_consistent_change_without_mouse_and_isolated_error(self):
        filter_state = BigMapViewFilter()
        first = filter_state.update((0, 0), 55, 8, (1920, 1080))
        for _ in range(10):
            filter_state.update((0, 0), 55, 8, (1920, 1080))
        self.assertEqual(filter_state.update((9000, 9000), 90, 8, (1920, 1080)), first)
        self.assertEqual(filter_state.update((0, 0), 55, 8, (1920, 1080)), first)
        for _ in range(7):
            final = filter_state.update((2000, 1000), 45, 8, (1920, 1080))
        self.assertEqual(final, ((2000.0, 1000.0), 45.0))

    def test_existing_mouse_input_timing(self):
        estimator = ViewStateEstimator()
        estimator.on_match((0, 0), 55, t=0)
        self.assertFalse(estimator.recently_interacting(now=0))
        estimator.on_mouse_press(100, 100, 'left', 1)
        self.assertTrue(estimator.recently_interacting(now=1.1))
        estimator.on_mouse_drag(110, 100, 10, 0, ('left',), 1.2)
        self.assertEqual(estimator.state(now=1.2).center, (-550, 0))
        estimator.on_mouse_release(110, 100, 'left', 1.3)
        self.assertTrue(estimator.recently_interacting(now=1.9))
        self.assertFalse(estimator.recently_interacting(now=2.1))
        estimator.on_mouse_scroll(110, 100, 0, 1, 3)
        self.assertTrue(estimator.recently_interacting(now=3.5))
        self.assertLess(estimator.state(now=3.5).game_scale, 55)

    def test_recorded_stationary_matches(self):
        coords = json.loads((ROOT / 'assets/stitched/map_coords.json').read_text(encoding='utf-8'))
        records = []
        expression = re.compile(r'\[MapFallback\] map=(\d+).*scale=([\d.]+).*game=\(([-\d]+),([-\d]+)\)')
        for line in (ROOT / 'logs/ok-script.log').read_text(encoding='utf-8').splitlines():
            if not '2026-10-04 11:18:00' <= line[:19] <= '2026-10-04 11:18:12':
                continue
            match = expression.search(line)
            if match:
                map_id, scale, x, y = match.groups()
                records.append((map_id, float(scale) * coords[map_id]['scale'][0],
                                (float(x), float(y))))
        self.assertGreater(len(records), 20)
        filter_state = BigMapViewFilter()
        accepted = [filter_state.update(center, scale, map_id, (1920, 1080))
                    for map_id, scale, center in records]
        changes = sum(left != right for left, right in zip(accepted, accepted[1:]))
        raw_changes = sum(left != right for left, right in zip(records, records[1:]))
        self.assertLess(changes, raw_changes / 10)
        self.assertLessEqual(len(set(accepted[-15:])), 2)
        print(f'真实日志回放：{len(records)} 次匹配，原始变化 {raw_changes} 次，稳定输出变化 {changes} 次')


if __name__ == '__main__':
    unittest.main()
