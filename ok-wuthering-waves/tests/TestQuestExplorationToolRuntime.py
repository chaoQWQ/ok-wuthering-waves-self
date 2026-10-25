from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

import cv2

sys.argv[0] = str(Path(__file__).resolve().parent.parent / "main.py")

from config import config
from ok import OK
from src.task.QuestStoryTask import QuestStoryTask
from src.utils.QuestAreaSearch import minimap_visible
from src.utils.QuestPuzzleSession import PuzzleObservation, QuestPuzzleSession, object_appearance


class TestQuestExplorationToolRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = Path(__file__).resolve().parent / "images"
        cls.data_directory = tempfile.TemporaryDirectory(prefix="quest-tool-runtime-", dir=cls.images.parents[2] / "cache")
        runtime_config = deepcopy(config)
        runtime_config.update({
            "check_mutex": False,
            "gui": None,
            "onetime_tasks": [["src.task.QuestStoryTask", "QuestStoryTask"]],
            "trigger_tasks": [],
            "disable_file_log": True,
            "config_folder": str(Path(cls.data_directory.name) / "configs"),
            "screenshots_folder": str(Path(cls.data_directory.name) / "screenshots"),
            "windows": None,
        })
        cls.runtime = OK(runtime_config)
        cls.task = cls.runtime.get_onetime_task(QuestStoryTask)
        cls.frame = cls.read_image("quest_tool_camera_world.png")

    @classmethod
    def tearDownClass(cls):
        cls.runtime.quit()
        cls.data_directory.cleanup()

    @classmethod
    def read_image(cls, name):
        path = cls.images / name
        frame = cv2.imread(str(path))
        if frame is None:
            raise FileNotFoundError(str(path))
        return frame

    def observation(self, frame):
        height, width = frame.shape[:2]
        return PuzzleObservation(
            "", "", (), phase="world" if minimap_visible(frame) else "unknown",
            appearance=object_appearance(frame, (width // 4, height // 5, width // 2, height // 2)),
            frame=frame, exploration_tool_ready=self.task._exploration_tool_ready(frame),
        )

    def test_runtime_feature_coordinates_at_multiple_resolutions(self):
        for width in (960, 1280, 1920, 2560, 3840):
            with self.subTest(width=width):
                frame = cv2.resize(self.frame, (width, round(width * self.frame.shape[0] / self.frame.shape[1])))
                self.assertTrue(self.task._exploration_tool_ready(frame))

    def test_world_and_other_interfaces_have_no_highlight(self):
        for name in ("quest_yangyang_interaction.png", "quest_after_story_combat.png",
                     "quest_area_runtime.png", "quest_story_synopsis.png",
                     "quest_teleport_closer_dialog.png", "quest_domain_confirmation.png"):
            with self.subTest(image=name):
                self.assertFalse(self.task._exploration_tool_ready(self.read_image(name)))

    def test_real_highlight_prepares_t_and_waits_for_observed_effect(self):
        observation = self.observation(self.frame)
        self.assertEqual(observation.phase, "world")
        session = QuestPuzzleSession()
        step, handler = session.prepare(observation, "skill")
        self.assertEqual((step.kind, step.action, step.key), ("exploration_tool", "skill", "t"))
        self.assertTrue({"dialog_started", "cutscene_started", "panel_started"}.issubset(step.expected_effect))
        session.begin(step, handler, observation, 0.0)
        self.assertEqual(session.verify(observation, 1.0), "pending")
        self.assertEqual(session.verify(observation, 3.0), "no_observed_progress")

    def test_actual_disappearance_confirms_tool_use(self):
        before = self.observation(self.frame)
        after = self.observation(self.read_image("quest_area_runtime.png"))
        self.assertEqual(after.phase, "world")
        self.assertFalse(after.exploration_tool_ready)
        session = QuestPuzzleSession()
        step, handler = session.prepare(before, "skill")
        session.begin(step, handler, before, 0.0)
        self.assertEqual(session.verify(after, 1.0), "tool_consumed")

    def test_tool_identity_is_independent_of_world_objects(self):
        session = QuestPuzzleSession()
        world_object = session.identify(self.observation(self.read_image("quest_area_runtime.png")))
        step, _ = session.prepare(self.observation(self.frame), "skill")
        self.assertNotEqual(step.object_id, world_object.object_id)
        tool = session.objects[step.object_id]
        self.assertEqual(tool.object_id, "exploration_tool")
        self.assertIsNone(tool.position)
        self.assertIsNone(tool.appearance)
        next_step, _ = session.prepare(self.observation(self.frame), "skill")
        self.assertEqual(next_step.object_id, step.object_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
