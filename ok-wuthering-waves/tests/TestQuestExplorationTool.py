from pathlib import Path
import unittest

import cv2

from src.utils.QuestExplorationTool import ExplorationToolTrigger, detect_exploration_tool


class TestQuestExplorationTool(unittest.TestCase):
    images = Path(__file__).resolve().parent / 'images'
    tool_box = (62, 65, 49, 44)

    def read_image(self, name):
        frame = cv2.imread(str(self.images / f'{name}.png'))
        if frame is None:
            raise FileNotFoundError(name)
        return frame

    def detection(self, name, scale=1.0):
        frame = self.read_image(name)
        if scale != 1.0:
            frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        box = tuple(round(value * scale) for value in self.tool_box)
        return detect_exploration_tool(frame, box)

    def test_highlighted_camera_at_multiple_resolutions(self):
        for name in ('quest_tool_highlight_camera', 'quest_tool_highlight_camera_2'):
            for scale in (.5, 2 / 3, 1.0, 4 / 3, 2.0):
                with self.subTest(image=name, scale=scale):
                    self.assertTrue(self.detection(name, scale))

    def test_idle_tools_and_colored_background_at_multiple_resolutions(self):
        for name in ('quest_tool_camera_idle', 'quest_tool_hook_yellow_background',
                     'quest_tool_sensor_idle', 'quest_tool_hook_idle', 'quest_tool_blue_background'):
            for scale in (.5, 2 / 3, 1.0, 4 / 3, 2.0):
                with self.subTest(image=name, scale=scale):
                    self.assertFalse(self.detection(name, scale))

    def test_blue_highlight_at_multiple_resolutions(self):
        for name in ('quest_tool_highlight_blue', 'quest_tool_highlight_blue_2',
                     'quest_tool_highlight_blue_recorded'):
            for scale in (.5, 2 / 3, .8, 1.0, 4 / 3, 2.0):
                with self.subTest(image=name, scale=scale):
                    self.assertTrue(self.detection(name, scale))

    def test_continuous_highlight_triggers_once(self):
        for name in ('quest_tool_highlight_camera', 'quest_tool_highlight_blue'):
            with self.subTest(image=name):
                ready = self.detection(name)
                trigger = ExplorationToolTrigger()
                self.assertTrue(trigger.observe(ready, 0.0))
                trigger.mark_used(0.0)
                self.assertFalse(trigger.observe(ready, 2.0))
                self.assertFalse(trigger.observe(ready, 30.0))

    def test_blue_highlight_triggers_after_previous_tool_use(self):
        trigger = ExplorationToolTrigger()
        self.assertTrue(trigger.observe(self.detection('quest_tool_highlight_camera'), 0.0))
        trigger.mark_used(0.0)
        self.assertFalse(trigger.observe(self.detection('quest_tool_camera_idle'), 1.0))
        self.assertFalse(trigger.observe(self.detection('quest_tool_camera_idle'), 1.6))
        self.assertTrue(trigger.observe(self.detection('quest_tool_highlight_blue'), 2.0))

    def test_brief_highlight_interruption_keeps_current_trigger(self):
        ready = self.detection('quest_tool_highlight_camera')
        unready = self.detection('quest_tool_camera_idle')
        trigger = ExplorationToolTrigger()
        self.assertTrue(trigger.observe(ready, 0.0))
        trigger.mark_used(0.0)
        self.assertFalse(trigger.observe(unready, 3.0))
        self.assertFalse(trigger.observe(unready, 3.3))
        self.assertFalse(trigger.observe(ready, 3.4))

    def test_new_highlight_requires_confirmation_and_cooldown(self):
        ready = self.detection('quest_tool_highlight_camera')
        unready = self.detection('quest_tool_camera_idle')
        trigger = ExplorationToolTrigger()
        self.assertTrue(trigger.observe(ready, 0.0))
        trigger.mark_used(0.0)
        self.assertFalse(trigger.observe(unready, .2))
        self.assertFalse(trigger.observe(unready, .8))
        self.assertFalse(trigger.observe(ready, 1.0))
        self.assertTrue(trigger.observe(ready, 2.0))
        trigger.mark_used(2.0)
        self.assertFalse(trigger.observe(ready, 4.0))

    def test_invalid_image_and_tool_region_raise(self):
        frame = self.read_image('quest_tool_highlight_camera')
        with self.assertRaises(ValueError):
            detect_exploration_tool(None, self.tool_box)
        with self.assertRaises(ValueError):
            detect_exploration_tool(frame, (0, 0, 0, 20))
        with self.assertRaises(ValueError):
            detect_exploration_tool(frame, (frame.shape[1], 0, 49, 44))


if __name__ == '__main__':
    unittest.main()
