import json
from pathlib import Path
import unittest

import cv2
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestDecisionEngine import parse_jev_response
from src.utils.QuestOcrPrivacy import is_named_quest_interaction
from src.utils.QuestVision import detect_interact_action
from tests.quest_interaction_observations import read_interaction_observations


class TestQuestInteraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(__file__).parent
        cls.frame = cv2.imread(str(cls.directory / "images/quest_yangyang_interaction.png"))
        if cls.frame is None:
            raise FileNotFoundError("秧秧交互截图不存在")
        cls.engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)

    def test_actual_named_interaction_at_multiple_resolutions(self):
        for width in (1024, 1280, 1817, 1920):
            with self.subTest(width=width):
                scale = width / self.frame.shape[1]
                frame = cv2.resize(self.frame, (width, round(self.frame.shape[0] * scale)))
                goal, interaction, distance, _ = read_interaction_observations(frame, self.engine)
                self.assertTrue(interaction.has_f)
                self.assertEqual(interaction.action_text, "秧秧")
                self.assertEqual(distance, 1)
                self.assertTrue(is_named_quest_interaction(goal, interaction.action_text))

    def test_actual_navigation_scenes_have_no_f_key(self):
        for name in ("quest_navigation_start.png", "quest_navigation_wrong_direction.png", "quest_rock_obstruction.png", "quest_after_story_combat.png"):
            with self.subTest(name=name):
                frame = cv2.imread(str(self.directory / "images" / name))
                self.assertIsNotNone(frame)
                self.assertFalse(detect_interact_action(frame).has_f)
                _, interaction, _, _ = read_interaction_observations(frame, self.engine)
                self.assertFalse(interaction.has_f)

    def test_actual_jev_choice_accepts_matching_quest_person(self):
        goal, interaction, distance, _ = read_interaction_observations(self.frame, self.engine)
        response = json.loads((self.directory / "data/quest_interaction_jev_response.json").read_text(encoding="utf-8"))
        action = parse_jev_response(response)
        self.assertEqual(action.action_type, "interact")
        self.assertTrue(action.confident)
        self.assertEqual(action.key, "f")
        self.assertLessEqual(distance, 8)
        self.assertTrue(is_named_quest_interaction(goal, interaction.action_text))

    def test_actual_flower_objective_does_not_match_person_interaction(self):
        frame = cv2.imread(str(self.directory / "images/quest_rock_obstruction.png"))
        self.assertIsNotNone(frame)
        goal, _, _, _ = read_interaction_observations(frame, self.engine)
        _, interaction, _, _ = read_interaction_observations(self.frame, self.engine)
        self.assertIn("跟随花朵", goal)
        self.assertFalse(is_named_quest_interaction(goal, interaction.action_text))


if __name__ == "__main__":
    unittest.main()
