import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.utils.QuestOcrPrivacy import sanitize_quest_text


@dataclass
class QuestAction:
    action_type: str  # 'interact', 'attack', 'skill', 'click', 'wait', 'climb_drop', 'turn', 'walk'
    key: Optional[str]  # 'f', 'e', 'x', 'w', 'space', None
    description: str
    wait_seconds: float
    turn_pixels: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


def decide_local(
    has_f_button: bool,
    action_text: str = "",
    quest_goal_text: str = ""
) -> Optional[QuestAction]:
    if has_f_button:
        desc = action_text if action_text else "触发任务交互"
        return QuestAction(
            action_type="interact",
            key="f",
            description=desc,
            wait_seconds=1.5
        )
    return None


def build_jev_payload(
    frame: np.ndarray,
    quest_goal_text: str,
    is_frozen_letterbox: bool = False,
    is_navigation_guidance: bool = False,
    ocr_text: str = "",
) -> dict:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    prompt_context = f"当前任务指引目标：{sanitize_quest_text(quest_goal_text)}。"
    clean_ocr_text = sanitize_quest_text(ocr_text)
    if clean_ocr_text:
        prompt_context += f"\n画面 OCR 文字：\n{clean_ocr_text}\n"
    if is_frozen_letterbox:
        prompt_context += (
            "检测到当前处于黑边剧情动画状态，且画面持续静止超过30秒无变化，"
            "判断可能正在等待交互输入以推进剧情。"
        )
        questions = {
            "action": {
                "type": "choice",
                "instructions": "选择当前推进剧情的最佳操作",
                "criteria": {
                    "interact": "按下交互按键 F 触发场景或人物对话推进",
                    "click": "点击画面中央或跳过对白进入下一阶段",
                    "wait": "等待过场动画自然播放完毕"
                }
            }
        }
    elif is_navigation_guidance:
        prompt_context += (
            "角色当前正在寻找路线前进。请根据地形、道路、墙体与障碍物选择动作："
            "若贴墙爬墙受阻则脱离攀爬；道路在开阔侧则转向道路；平坦通路则向前推进。"
        )
        questions = {
            "action": {
                "type": "choice",
                "instructions": "选择避让障碍物并继续前进的最佳操作",
                "criteria": {
                    "turn_left": "向左旋转视角朝向开阔通道",
                    "turn_right": "向右旋转视角朝向开阔通道",
                    "climb_drop": "松开墙体并后撤脱离攀爬状态",
                    "walk_forward": "沿平坦道路向前行进"
                }
            }
        }
    else:
        prompt_context += "角色已移动到达目标任务位置，需要判定接下来的具体操作。"
        questions = {
            "action": {
                "type": "choice",
                "instructions": "选择到达目标位置后的操作",
                "criteria": {
                    "interact": "按下交互按键 F 触发场景交互推进",
                    "attack": "普通攻击破坏目标障碍物",
                    "wait": "原地等待目标状态刷新"
                }
            }
        }

    return {
        "model": "jev-latest",
        "state": prompt_context,
        "questions": questions
    }


def parse_jev_response(response_json: dict) -> QuestAction:
    if not isinstance(response_json, dict):
        raise ValueError("模型返回数据必须为字典格式")

    # 1. 兼容 TypeSafe Jev System One 规范
    if "answers" in response_json:
        answers = response_json["answers"]
        if not isinstance(answers, dict):
            raise ValueError("answers 字段必须为字典格式")
        action_item = answers.get("action")
        if not isinstance(action_item, dict):
            raise ValueError("answers 缺少 action 字段")
        choice = action_item.get("choice")
        if not choice or not isinstance(choice, str):
            raise ValueError("action 缺少有效的 choice 字段")

        # 映射 System One 决策动作
        action_mapping = {
            "turn_left": ("turn", None, "向左旋转视角朝向开阔通路", 0.2, -180),
            "turn_right": ("turn", None, "向右旋转视角朝向开阔通路", 0.2, 180),
            "climb_drop": ("climb_drop", "x", "松开墙体并后撤脱离攀爬", 0.4, 0),
            "walk_forward": ("walk", "w", "沿平坦通道向前行进", 0.4, 0),
            "interact": ("interact", "f", "触发交互推进剧情", 1.5, 0),
            "click": ("click", None, "点击画面推进剧情", 0.5, 0),
            "attack": ("attack", "j", "执行攻击破坏目标", 0.5, 0),
            "wait": ("wait", None, "原地等待状态推进", 1.0, 0),
        }

        if choice not in action_mapping:
            raise ValueError(f"未知的选择动作: {choice}")

        act_type, act_key, act_desc, act_wait, act_turn = action_mapping[choice]
        usage = response_json.get("usage", {}) if isinstance(response_json, dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
        cost = float(response_json.get("cost", 0.0) or usage.get("cost", 0.0) or 0.0)

        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens if (input_tokens or output_tokens) else 433

        return QuestAction(
            action_type=act_type,
            key=act_key,
            description=act_desc,
            wait_seconds=act_wait,
            turn_pixels=act_turn,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost
        )

    # 2. 兼容通用聊天补全格式
    choices = response_json.get("choices")
    if not choices or not isinstance(choices, list):
        raise ValueError("模型返回缺少 choices 或 answers 字段")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if not content:
        raise ValueError("模型返回内容为空")

    content_clean = content.strip()
    if content_clean.startswith("```json"):
        content_clean = content_clean[7:]
    if content_clean.startswith("```"):
        content_clean = content_clean[3:]
    if content_clean.endswith("```"):
        content_clean = content_clean[:-3]
    content_clean = content_clean.strip()

    data = json.loads(content_clean)
    action_type = data.get("action")
    valid_actions = ("interact", "attack", "skill", "click", "wait", "climb_drop", "turn", "walk")
    if not action_type or action_type not in valid_actions:
        raise ValueError(f"未知的动作类型: {action_type}")

    key = data.get("key")
    description = data.get("description", "执行决策操作")
    wait_seconds = float(data.get("wait_seconds", 1.0))
    turn_pixels = int(data.get("turn_pixels", 0))

    usage = response_json.get("usage", {}) if isinstance(response_json, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
    cost = float(response_json.get("cost", 0.0) or usage.get("cost", 0.0) or 0.0)

    if total_tokens == 0:
        total_tokens = 800

    return QuestAction(
        action_type=action_type,
        key=key,
        description=description,
        wait_seconds=wait_seconds,
        turn_pixels=turn_pixels,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost=cost
    )



DEFAULT_JEV_API_URL = "https://api.typesafe.ai/v1/systemone"


def decide_via_jev(
    frame: np.ndarray,
    quest_goal_text: str,
    api_url: str,
    api_key: str,
    is_frozen_letterbox: bool = False,
    is_navigation_guidance: bool = False,
    timeout_seconds: float = 12.0,
    ocr_text: str = "",
) -> QuestAction:
    endpoint = api_url.strip() if api_url and api_url.strip() else DEFAULT_JEV_API_URL
    if not endpoint.startswith("http"):
        raise ValueError("API 端点地址无效")
    if not api_key:
        raise ValueError("API Key 密钥不能为空")

    payload = build_jev_payload(
        frame,
        quest_goal_text,
        is_frozen_letterbox=is_frozen_letterbox,
        is_navigation_guidance=is_navigation_guidance,
        ocr_text=ocr_text,
    )
    req_body = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    req = urllib.request.Request(endpoint, data=req_body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise RuntimeError(f"API 请求失败，HTTP 状态码: {response.status}")
            resp_bytes = response.read()
            resp_dict = json.loads(resp_bytes.decode("utf-8"))
            return parse_jev_response(resp_dict)
    except urllib.error.URLError as err:
        raise RuntimeError(f"连接 API 失败: {err}") from err


def decide_quest_action(
    frame: np.ndarray,
    has_f_button: bool = False,
    action_text: str = "",
    quest_goal_text: str = "",
    is_frozen_letterbox: bool = False,
    is_navigation_guidance: bool = False,
    api_url: str = "",
    api_key: str = "",
    ocr_text: str = "",
) -> QuestAction:
    if not is_frozen_letterbox and not is_navigation_guidance:
        local_action = decide_local(has_f_button, action_text, quest_goal_text)
        if local_action is not None:
            return local_action

    return decide_via_jev(
        frame=frame,
        quest_goal_text=quest_goal_text,
        api_url=api_url,
        api_key=api_key,
        is_frozen_letterbox=is_frozen_letterbox,
        is_navigation_guidance=is_navigation_guidance,
        ocr_text=ocr_text,
    )
