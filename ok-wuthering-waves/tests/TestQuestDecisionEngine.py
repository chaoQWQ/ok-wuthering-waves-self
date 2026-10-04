import unittest
import numpy as np

from src.utils.QuestDecisionEngine import (
    QuestAction,
    build_jev_payload,
    decide_local,
    decide_quest_action,
    decide_via_jev,
    parse_jev_response,
)


class TestQuestDecisionEngine(unittest.TestCase):

    def test_decide_local_with_f_button(self):
        action = decide_local(
            has_f_button=True,
            action_text="进入雾隐阁",
            quest_goal_text="从正北方向进入雾隐阁"
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "interact")
        self.assertEqual(action.key, "f")
        self.assertEqual(action.description, "进入雾隐阁")

    def test_decide_local_without_f_button(self):
        action = decide_local(
            has_f_button=False,
            action_text="",
            quest_goal_text="寻找目标"
        )
        self.assertIsNone(action)

    def test_build_jev_payload_structure(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        payload = build_jev_payload(
            frame=frame,
            quest_goal_text="击碎发光晶体",
            is_frozen_letterbox=False,
            is_navigation_guidance=True
        )
        self.assertEqual(payload["model"], "jev-latest")
        self.assertIn("击碎发光晶体", payload["state"])
        self.assertIn("action", payload["questions"])
        criteria = payload["questions"]["action"]["criteria"]
        self.assertIn("turn_left", criteria)
        self.assertIn("climb_drop", criteria)

    def test_build_jev_payload_with_frozen_letterbox(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        payload = build_jev_payload(
            frame=frame,
            quest_goal_text="推进剧情",
            is_frozen_letterbox=True
        )
        self.assertEqual(payload["model"], "jev-latest")
        self.assertIn("黑边剧情动画", payload["state"])
        self.assertIn("持续静止超过30秒", payload["state"])
        self.assertIn("interact", payload["questions"]["action"]["criteria"])

    def test_build_jev_payload_empty_frame_raises(self):
        with self.assertRaises(ValueError):
            build_jev_payload(None, "推进剧情")

    def test_parse_jev_response_systemone_action(self):
        raw = {
            "answers": {
                "action": {
                    "type": "choice",
                    "choice": "turn_left",
                    "confidence": 0.85
                }
            },
            "usage": {
                "input_tokens": 382,
                "output_tokens": 51
            }
        }
        action = parse_jev_response(raw)
        self.assertEqual(action.action_type, "turn")
        self.assertEqual(action.turn_pixels, -180)
        self.assertEqual(action.prompt_tokens, 382)
        self.assertEqual(action.completion_tokens, 51)
        self.assertEqual(action.total_tokens, 433)

    def test_parse_jev_response_systemone_climb_drop(self):
        raw = {
            "answers": {
                "action": {
                    "type": "choice",
                    "choice": "climb_drop",
                    "confidence": 0.92
                }
            },
            "usage": {
                "input_tokens": 400,
                "output_tokens": 40
            }
        }
        action = parse_jev_response(raw)
        self.assertEqual(action.action_type, "climb_drop")
        self.assertEqual(action.key, "x")
        self.assertEqual(action.total_tokens, 440)

    def test_parse_jev_response_clean_json(self):
        raw = {
            "choices": [
                {
                    "message": {
                        "content": '{"action": "interact", "key": "f", "description": "确认对话", "wait_seconds": 1.5}'
                    }
                }
            ]
        }
        action = parse_jev_response(raw)
        self.assertEqual(action.action_type, "interact")
        self.assertEqual(action.key, "f")
        self.assertEqual(action.description, "确认对话")
        self.assertEqual(action.wait_seconds, 1.5)

    def test_parse_jev_response_markdown_json(self):
        raw = {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"action": "click", "key": null, "description": "点击画面继续", "wait_seconds": 2.0}\n```'
                    }
                }
            ]
        }
        action = parse_jev_response(raw)
        self.assertEqual(action.action_type, "click")
        self.assertIsNone(action.key)
        self.assertEqual(action.description, "点击画面继续")
        self.assertEqual(action.wait_seconds, 2.0)

    def test_parse_jev_response_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            parse_jev_response({})
        with self.assertRaises(ValueError):
            parse_jev_response({"choices": []})
        with self.assertRaises(ValueError):
            parse_jev_response({"answers": {}})
        with self.assertRaises(ValueError):
            parse_jev_response({"answers": {"action": {"choice": "invalid_choice"}}})

    def test_decide_via_jev_fast_fail_invalid_url_or_key(self):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        # 验证非 http 开头的非法端点就地抛出 ValueError
        with self.assertRaises(ValueError):
            decide_via_jev(frame, "任务目标", api_url="ftp://invalid", api_key="secret")
        # 验证密钥为空时就地抛出 ValueError
        with self.assertRaises(ValueError):
            decide_via_jev(frame, "任务目标", api_url="", api_key="")

    def test_decide_quest_action_priority_local(self):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        action = decide_quest_action(
            frame=frame,
            has_f_button=True,
            action_text="对话",
            quest_goal_text="交谈",
            is_frozen_letterbox=False,
            api_url="",
            api_key=""
        )
        self.assertEqual(action.action_type, "interact")
        self.assertEqual(action.key, "f")


if __name__ == "__main__":
    unittest.main()
