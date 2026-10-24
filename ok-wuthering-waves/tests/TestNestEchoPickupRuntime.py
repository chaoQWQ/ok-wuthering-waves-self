import tempfile
import unittest
from pathlib import Path

import cv2

from config import config
from ok import OK, Box, ImageCaptureMethod
from src.task.NightmareNestTask import (
    NEST_ABSORB_TEXT, NEST_ECHO_THRESHOLD, NestPickupState, NestTarget, NightmareNestTask,
)
from src.utils.EchoApproach import EchoApproach


class TestNestPickupState(unittest.TestCase):
    def test_three_attempts_include_initial_attempt(self):
        state = NestPickupState()
        for attempt in range(1, 4):
            self.assertTrue(state.begin_attempt())
            self.assertEqual(attempt, state.attempts)
        for _ in range(5):
            self.assertFalse(state.begin_attempt())
            self.assertEqual(3, state.attempts)

    def test_partial_collection_keeps_attempt_budget(self):
        state = NestPickupState()
        self.assertTrue(state.begin_attempt())
        state.collected = True
        self.assertTrue(state.begin_attempt())
        self.assertTrue(state.collected)
        self.assertEqual(2, state.attempts)
        self.assertFalse(state.complete)
        self.assertTrue(state.begin_attempt())
        self.assertFalse(state.begin_attempt())


class TestNestEchoPickupRuntime(unittest.TestCase):
    def test_real_task_budget_and_real_game_images(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1] / 'cache') as directory:
            runtime_config = dict(config)
            runtime_config.update({
                'gui': None,
                'check_mutex': False,
                'custom_tasks': False,
                'disable_file_log': True,
                'config_folder': str(Path(directory) / 'configs'),
                'screenshots_folder': str(Path(directory) / 'screenshots'),
                'onetime_tasks': [['src.task.NightmareNestTask', 'NightmareNestTask']],
                'trigger_tasks': [],
            })
            runtime = OK(runtime_config)
            try:
                executor = runtime.task_executor
                executor.paused = False
                task = executor.get_task_by_class(NightmareNestTask)
                target = NestTarget(Box(1700, 500, 1, 1), 'go_nest:41:25')
                state = task._get_nest_pickup_state(target)
                self.assertTrue(state.begin_attempt())
                reopened = NestTarget(Box(1701, 501, 1, 1), target.cache_key)
                self.assertIs(state, task._get_nest_pickup_state(reopened))
                self.assertTrue(task._get_nest_pickup_state(reopened).begin_attempt())
                self.assertTrue(task._get_nest_pickup_state(target).begin_attempt())
                self.assertFalse(task._get_nest_pickup_state(reopened).begin_attempt())
                other = NestTarget(Box(1700, 700, 1, 1), 'go_nest:48:35')
                self.assertEqual(0, task._get_nest_pickup_state(other).attempts)
                self.assertTrue(task._get_nest_pickup_state(other).begin_attempt())
                self.assertEqual((False, False), task._collect_nest_echoes(time_out=0))
                self.assertEqual((False, False), task._collect_nest_echoes(time_out=0, walk_from_entry=True))
                self.assertFalse(task._walk_nest_entry_for_pickup(time_out=0))

                images = Path(__file__).parent / 'images'
                for name, absorb_visible in (
                        ('nest_echo_uncollected.png', False), ('nest_echo_crowd.png', False),
                        ('absorb.png', True)):
                    original = cv2.imread(str(images / name))
                    if original is None:
                        raise FileNotFoundError(name)
                    for width, height in ((1920, 1080), (1280, 720)):
                        with self.subTest(image=name, resolution=(width, height)):
                            frame = cv2.resize(original, (width, height))
                            # 使用真实截图直接执行模型推理和 OCR，执行线程保持未启动。
                            image_path = Path(directory) / f'{width}_{name}'
                            if not cv2.imwrite(str(image_path), frame):
                                raise OSError(str(image_path))
                            capture = ImageCaptureMethod(runtime.exit_event, [str(image_path)])
                            runtime.device_manager.capture_method = capture
                            executor._frame = frame
                            executor.feature_set.check_size(frame)
                            echoes = task.find_echos(threshold=NEST_ECHO_THRESHOLD)
                            self.assertGreater(len(echoes), 0)
                            raw_echoes = task.find_echos(threshold=NEST_ECHO_THRESHOLD, anchor=False)
                            self.assertTrue(all(box.height > 1 for box in raw_echoes))
                            approach = EchoApproach(threshold=NEST_ECHO_THRESHOLD)
                            decisions = []
                            for index in range(12):
                                # 检测结果的顺序变化保持原始识别坐标和置信度。
                                ordered = raw_echoes if index % 2 == 0 else list(reversed(raw_echoes))
                                decisions.append(approach.update(ordered, width, height, index / 15))
                            self.assertIsNone(decisions[0].target_id)
                            self.assertEqual((), decisions[0].keys)
                            confirmed = decisions[1:]
                            self.assertIsNotNone(confirmed[0].target_id)
                            self.assertEqual(1, len({result.target_id for result in confirmed}))
                            self.assertTrue(all('w' in result.keys for result in confirmed))
                            self.assertTrue(all(not {'a', 'd'}.issubset(result.keys) for result in confirmed))
                            self.assertEqual(1, len({result.keys for result in confirmed}))
                            prompt = task.find_f_with_text(target_text=NEST_ABSORB_TEXT)
                            self.assertEqual(absorb_visible, bool(prompt))
                            if not absorb_visible:
                                self.assertFalse(task._pick_nest_echo())
                                self.assertFalse(task._capture_success)
            finally:
                runtime.quit()
                runtime.task_executor.destroy()
                runtime.device_manager.close()


if __name__ == '__main__':
    unittest.main()
