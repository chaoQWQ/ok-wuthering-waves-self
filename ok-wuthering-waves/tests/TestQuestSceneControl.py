import base64
import json
from pathlib import Path
import unittest

import cv2
import numpy as np
from onnxocr.onnx_paddleocr import ONNXPaddleOcr

from src.utils.QuestBackgroundMotion import detect_background_motion
from src.utils.QuestAreaSearch import minimap_visible
from src.utils.QuestDecisionEngine import DETOUR_SIDE_KEYS, encode_frame_as_data_url, parse_jev_response
from src.utils.QuestDecisionSession import QuestDecisionSession
from src.utils.QuestOcrPrivacy import quest_goal_from_lines
from src.utils.QuestProgressTracker import QuestProgressTracker
from src.utils.QuestPuzzleSession import ExplicitControlHandler, PuzzleObservation, QuestPuzzleSession, object_appearance
from src.utils.QuestSceneState import QuestSceneState
from src.utils.QuestTraversalController import QuestTraversalController
from src.utils.QuestVision import detect_climbing_stamina, detect_quest_vertical_hint, parse_distance_text
from tests.quest_interaction_observations import read_interaction_observations


class TestQuestSceneControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(__file__).parent
        cls.engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, use_npu=True)
        cls.frames = {}
        cls.observations = {}
        for name in ("quest_yangyang_interaction", "quest_navigation_start", "quest_climbing", "quest_rock_obstruction", "quest_after_story_combat"):
            frame = cv2.imread(str(cls.directory / "images" / (name + ".png")))
            if frame is None:
                raise FileNotFoundError(name)
            cls.frames[name] = frame
            goal, interaction, _, text = read_interaction_observations(frame, cls.engine)
            appearance = object_appearance(frame, (frame.shape[1] // 4, frame.shape[0] // 5,
                                                   frame.shape[1] // 2, frame.shape[0] // 2))
            cls.observations[name] = PuzzleObservation(goal, interaction.action_text, tuple(text.splitlines()), appearance=appearance)

    def test_actual_npc_interaction_requires_observed_effect(self):
        observation = self.observations["quest_yangyang_interaction"]
        session = QuestPuzzleSession()
        step, handler = session.prepare(observation, "interact")
        self.assertEqual(step.kind, "dialog")
        self.assertEqual(step.preconditions["interaction"], "秧秧")
        session.begin(step, handler, observation, 0)
        self.assertEqual(session.verify(observation, 2), "pending")
        self.assertEqual(session.verify(observation, 3), "no_observed_progress")
        self.assertEqual(session.completed_steps, [])

    def test_same_object_stops_after_two_unproductive_operations(self):
        observation = self.observations["quest_yangyang_interaction"]
        session = QuestPuzzleSession()
        for attempt in range(2):
            step, handler = session.prepare(observation, "interact")
            session.begin(step, handler, observation, attempt * 5)
            self.assertEqual(session.verify(observation, attempt * 5 + 3), "no_observed_progress")
        self.assertEqual(len(session.objects), 1)
        with self.assertRaises(RuntimeError):
            session.prepare(observation, "interact")

    def test_actual_movement_alone_does_not_confirm_interaction(self):
        observation = self.observations["quest_yangyang_interaction"]
        session = QuestPuzzleSession()
        step, handler = session.prepare(observation, "interact")
        session.begin(step, handler, observation, 0)
        self.assertEqual(session.verify(observation, 3, animation=True), "pending")
        self.assertEqual(session.verify(observation, 8, animation=True), "no_observed_progress")

    def test_actual_new_objective_confirms_transition(self):
        session = QuestPuzzleSession()
        before = self.observations["quest_yangyang_interaction"]
        after = self.observations["quest_after_story_combat"]
        step, handler = session.prepare(before, "interact")
        session.begin(step, handler, before, 0)
        self.assertEqual(session.verify(after, 1), "quest_changed")
        self.assertEqual(len(session.completed_steps), 1)

    def test_external_verification_preserves_pending_step_and_rate_limit(self):
        observation = self.observations["quest_yangyang_interaction"]
        session = QuestDecisionSession()
        session.observe(observation.goal, observation.interaction, 0)
        session.record("interact", observation.interaction, "near_interaction", 0, external_verification=True)
        session.observe(observation.goal, observation.interaction, 5)
        self.assertIsNotNone(session.pending)
        for second in range(12):
            session.reserve_call(second)
        session.mark_transition("combat_started")
        with self.assertRaises(RuntimeError):
            session.reserve_call(13)

    def test_traversal_decision_persists_until_scene_change(self):
        state = QuestSceneState()
        state.set_goal(self.observations["quest_navigation_start"].goal)
        controller = QuestTraversalController(state)
        controller.remember("walk")
        self.assertEqual(controller.current(), "walk")
        self.assertEqual(controller.current(), "walk")
        state.transition("combat")
        self.assertIsNone(controller.current())

    def test_unknown_motion_does_not_confirm_blockage(self):
        controller = QuestTraversalController(QuestSceneState())
        controller.remember("walk")
        for now in range(10):
            controller.record_progress(None, now)
        self.assertIsNone(controller.stationary_since)
        self.assertEqual(controller.current(), "walk")

    def test_stationary_climbing_invalidates_traversal(self):
        frame = self.frames["quest_climbing"]
        motion = detect_background_motion(frame, frame)
        self.assertFalse(motion.moving)
        controller = QuestTraversalController(QuestSceneState())
        controller.remember("climb")
        controller.record_progress(motion.moving, 0)
        controller.record_progress(motion.moving, 3)
        self.assertIsNone(controller.current())
        self.assertEqual(controller.failed_actions["climb"], 1)

    def test_climbing_ring_from_actual_screenshot(self):
        stamina = detect_climbing_stamina(self.frames["quest_climbing"])
        self.assertIsNotNone(stamina)
        self.assertGreater(stamina, .6)
        self.assertLessEqual(stamina, 1)
        self.assertIsNone(detect_climbing_stamina(self.frames["quest_navigation_start"]))

    def test_actual_distance_arrow_direction(self):
        expected = {"quest_climbing": "below", "quest_rock_obstruction": "above", "quest_navigation_start": "unknown"}
        for name, direction in expected.items():
            with self.subTest(name=name):
                frame = self.frames[name]
                h, w = frame.shape[:2]
                x, y = int(w * .01), int(h * .23)
                crop = frame[y:int(h * .43), x:int(w * .25)]
                boxes = self.engine.ocr(crop, cls=False)[0]
                hints = []
                for points, (text, _) in boxes:
                    if parse_distance_text(text, require_unit=True) is not None:
                        xs, ys = [p[0] for p in points], [p[1] for p in points]
                        hints.append(detect_quest_vertical_hint(frame, (int(x + min(xs)), int(y + min(ys)),
                                                                       int(max(xs) - min(xs)), int(max(ys) - min(ys)))))
                self.assertIn(direction, hints, [(text, points) for points, (text, _) in boxes if parse_distance_text(text, require_unit=True) is not None])

    def test_encoded_real_screenshot_excludes_identity(self):
        frame = self.frames["quest_navigation_start"]
        original = frame.copy()
        data = encode_frame_as_data_url(frame)
        decoded = cv2.imdecode(np.frombuffer(base64.b64decode(data.split(",", 1)[1]), dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = decoded.shape[:2]
        crop = decoded[int(h * .9):, int(w * .7):]
        self.assertLess(float(np.mean(crop)), 2)
        text = " ".join(item[1][0] for item in self.engine.ocr(crop, cls=False)[0])
        self.assertNotRegex(text, r"特征码|特征碼|\d{8,}")
        self.assertTrue(np.array_equal(original, frame))

    def test_recorded_jev_response_still_matches_actual_interaction(self):
        response = json.loads((self.directory / "data/quest_interaction_jev_response.json").read_text(encoding="utf-8"))
        action = parse_jev_response(response)
        session = QuestPuzzleSession()
        step, _ = session.prepare(self.observations["quest_yangyang_interaction"], action.action_type)
        self.assertEqual(step.key, "f")

    def test_captured_domain_confirmation_blocks_world_input(self):
        frame = cv2.imread(str(self.directory / "images/quest_domain_confirmation.png"))
        self.assertIsNotNone(frame)
        self.assertFalse(minimap_visible(frame))

    def test_retreat_decision_does_not_reenter_obstacle(self):
        tracker = QuestProgressTracker()
        tracker.begin_recovery(side_key=DETOUR_SIDE_KEYS["back"])
        while tracker.recovery_step is not None:
            keys, _ = tracker.next_recovery_movement()
            self.assertEqual(keys, ["s"])

    def test_actual_failed_camera_changes_request_new_observation(self):
        controller = QuestTraversalController(QuestSceneState())
        from src.utils.QuestVision import detect_quest_beacon
        errors = []
        for name in ("quest_navigation_start", "quest_navigation_wrong_direction", "quest_navigation_wrong_direction"):
            frame = self.frames.get(name)
            if frame is None:
                frame = cv2.imread(str(self.directory / "images" / (name + ".png")))
            beacon = detect_quest_beacon(frame)
            errors.append((beacon.x + beacon.width / 2 - frame.shape[1] / 2) / frame.shape[1] * 90)
        self.assertTrue(controller.observe_camera(errors[0]))
        self.assertTrue(controller.observe_camera(errors[1]))
        self.assertFalse(controller.observe_camera(errors[2]))

    def test_recorded_objective_counter_preserves_task_identity(self):
        lines = (self.directory / "data/quest_scene_objectives.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(quest_goal_from_lines([lines[0]]), quest_goal_from_lines([lines[1]]))
        self.assertNotEqual(quest_goal_from_lines([lines[1]]), quest_goal_from_lines([lines[2]]))

    def test_registered_handler_survives_objective_change(self):
        session = QuestPuzzleSession()
        handler = ExplicitControlHandler()
        session.register(handler)
        session.identify(self.observations["quest_yangyang_interaction"])
        session.reset_goal()
        self.assertIn(handler, session.handlers)
        self.assertEqual(session.objects, {})


if __name__ == "__main__":
    unittest.main()
