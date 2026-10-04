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
    action_type: str  # 'interact', 'attack', 'skill', 'click', 'wait'
    key: Optional[str]  # 'f', 'e', 'space', None
    description: str
    wait_seconds: float


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
    is_frozen_letterbox: bool = False
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
    else:
        prompt_context += "角色已移动到达目标任务位置，需要判定接下来的具体操作。\n"

    system_instruction = (
        "请根据提供的游戏画面与任务描述，输出下一步执行操作。\n"
        "返回结果必须为严格的 JSON 格式，包含以下字段：\n"
        "- action: 操作类型，可选 'interact', 'attack', 'skill', 'click', 'wait'\n"
        "- key: 按键名称，例如 'f', 'e', 'space'，无按键则为 null\n"
        "- description: 简要中文动作说明\n"
        "- wait_seconds: 操作执行后等待秒数，浮点数\n"
        "示例：{\"action\": \"interact\", \"key\": \"f\", \"description\": \"推进对话\", \"wait_seconds\": 1.5}"
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

    # 提取 JSON 块
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
    if not action_type or action_type not in ("interact", "attack", "skill", "click", "wait"):
        raise ValueError(f"未知的动作类型: {action_type}")

    key = data.get("key")
    description = data.get("description", "执行决策操作")
    wait_seconds = float(data.get("wait_seconds", 1.0))

    return QuestAction(
        action_type=action_type,
        key=key,
        description=description,
        wait_seconds=wait_seconds
    )


def decide_via_jev(
    frame: np.ndarray,
    quest_goal_text: str,
    api_url: str,
    api_key: str,
    is_frozen_letterbox: bool = False,
    timeout_seconds: float = 12.0
) -> QuestAction:
    if not api_url or not api_url.startswith("http"):
        raise ValueError("API 端点地址无效或为空")
    if not api_key:
        raise ValueError("API Key 密钥不能为空")

    payload = build_jev_payload(frame, quest_goal_text, is_frozen_letterbox=is_frozen_letterbox)
    req_body = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    req = urllib.request.Request(api_url, data=req_body, headers=headers, method="POST")
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
    has_f_button: bool,
    action_text: str = "",
    quest_goal_text: str = "",
    is_frozen_letterbox: bool = False,
    api_url: str = "",
    api_key: str = ""
) -> QuestAction:
    # 优先本地快速匹配明确的交互按钮
    if not is_frozen_letterbox:
        local_action = decide_local(has_f_button, action_text, quest_goal_text)
        if local_action is not None:
            return local_action

    # 无交互按键或黑边静止超时状态下使用 jev 视觉模型 API
    return decide_via_jev(
        frame=frame,
        quest_goal_text=quest_goal_text,
        api_url=api_url,
        api_key=api_key,
        is_frozen_letterbox=is_frozen_letterbox
    )
