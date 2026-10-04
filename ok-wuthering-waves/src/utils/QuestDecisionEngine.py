import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


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
    is_navigation_guidance: bool = False
) -> dict:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    # 压缩为 1280x720 以控制传输数据量
    height, width = frame.shape[:2]
    if width > 1280 or height > 720:
        target_w = 1280
        target_h = int(height * (1280.0 / width))
        resized = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
    else:
        resized = frame

    encode_success, buffer = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not encode_success:
        raise RuntimeError("画面压缩编码失败")

    base64_img = base64.b64encode(buffer).decode("ascii")

    prompt_context = f"当前任务指引目标：{quest_goal_text}。\n"
    if is_frozen_letterbox:
        prompt_context += (
            "检测到当前处于黑边剧情动画状态，且画面持续静止超过30秒无变化，"
            "判断可能正在等待交互输入以推进剧情。\n"
        )
    elif is_navigation_guidance:
        prompt_context += (
            "角色当前正在寻找路线前进。请观察画面中的地形、地面道路、走廊、大门与障碍物：\n"
            "如果角色正在贴墙或爬墙受阻，请指示脱离攀爬或后撤；\n"
            "如果道路在左侧或右侧开阔处，请指示镜头旋转朝向道路；\n"
            "指示走向平坦通路的合适动作。\n"
        )
    else:
        prompt_context += "角色已移动到达目标任务位置，需要判定接下来的具体操作。\n"

    system_instruction = (
        "请根据提供的游戏画面与任务描述，输出下一步执行操作。\n"
        "返回结果必须为严格的 JSON 格式，包含以下字段：\n"
        "- action: 操作类型，可选 'interact', 'attack', 'skill', 'click', 'wait', 'climb_drop', 'turn', 'walk'\n"
        "- key: 按键名称，例如 'f', 'e', 'x', 'w', 's'，无按键则为 null\n"
        "- description: 简要中文动作说明\n"
        "- wait_seconds: 操作执行后等待秒数，浮点数\n"
        "- turn_pixels: 镜头横向旋转像素估计值，向右为正向左为负，无需旋转为 0\n"
        "示例：{\"action\": \"turn\", \"key\": null, \"description\": \"向左旋转视角走向石板路\", \"wait_seconds\": 0.2, \"turn_pixels\": -120}"
    )

    return {
        "model": "jev-vision",
        "messages": [
            {"role": "system", "content": system_instruction},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_context},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}
                    }
                ]
            }
        ],
        "temperature": 0.1
    }


def parse_jev_response(response_json: dict) -> QuestAction:
    if not isinstance(response_json, dict):
        raise ValueError("模型返回数据必须为字典格式")

    choices = response_json.get("choices")
    if not choices or not isinstance(choices, list):
        raise ValueError("模型返回缺少 choices 字段")

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
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
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
    timeout_seconds: float = 12.0
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
        is_navigation_guidance=is_navigation_guidance
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
    api_key: str = ""
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
        is_navigation_guidance=is_navigation_guidance
    )

