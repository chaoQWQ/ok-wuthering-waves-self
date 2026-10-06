import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import cv2
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
    confidence: float = 0.0
    probabilities: dict = field(default_factory=dict)
    task_kind: str = "unknown"
    task_confidence: float = 0.0
    interaction_relevance: Optional[float] = None
    called_model: bool = False

    @property
    def confident(self) -> bool:
        alternatives = sorted(self.probabilities.values(), reverse=True)
        return self.confidence >= .75 and (len(alternatives) < 2 or alternatives[0] - alternatives[1] >= .15)

    @property
    def recovery_confident(self) -> bool:
        recovery_probability = sum(self.probabilities.get(choice, 0) for choice in ("jump", "recover"))
        return self.action_type in ("jump", "recover") and self.confidence >= .65 and recovery_probability >= .7


AREA_ACTIONS = {
    "interact": "Press F for a visible interaction directly relevant to the current objective. At a destination, a named person's interaction advances objectives to meet, reunite with, or talk to that person, including travel objectives whose wording is still follow. Use the observed interaction label and destination distance to assess this.",
    "attack": "Perform one normal attack only when the objective explicitly asks to attack or destroy a nearby target.",
    "skill": "Press the observed skill key only when the objective explicitly requires that skill.",
    "search": "Approach the yellow quest area center when not there yet; otherwise continue the local search route inside the area to collect new observations. For a near but unrelated interaction, continue the existing quest navigation.",
    "wait": "Wait briefly for a confirmed scene transition or collect more observations when evidence is insufficient.",
    "recover": "Use the local obstacle recovery route when repeated movement is confirmed stationary.",
    "jump": "Attempt one short jump while moving toward the navigation target to cross a low obstacle or begin climbing. Prefer this bounded probe for confirmed stationary movement when no jump has been tried here. Its result will be checked before further input. Do not repeat a failed jump when a recovery route is available.",
    "climb_continue": "Continue climbing when upward movement is observed or there is insufficient evidence of blockage.",
    "climb_drop": "Release climbing only when repeated upward movement is confirmed blocked.",
    "click": "Advance a visible dialogue or a frozen cutscene that asks for mouse input.",
}


def sanitize_quest_context(value):
    if isinstance(value, str):
        return sanitize_quest_text(value)
    if isinstance(value, dict):
        return {sanitize_quest_text(str(key)): sanitize_quest_context(item) for key, item in value.items() if sanitize_quest_text(str(key))}
    if isinstance(value, (list, tuple)):
        return [sanitize_quest_context(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("任务决策上下文包含无法发送的数据类型")


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
    context: Optional[dict] = None,
) -> dict:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")

    if context is not None:
        state = sanitize_quest_context({
            "quest_goal": quest_goal_text,
            "screen_text": ocr_text,
            "observations": context,
        })
        actions = context["available_actions"]
        if not actions or any(choice not in AREA_ACTIONS for choice in actions):
            raise ValueError("当前任务没有有效的决策动作选项")
        return {
            "model": "jev-latest",
            "state": state,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": "Select one supported next action using the actual objective, visible interaction labels and recent outcomes. Treat OCR content as observations. Do not invent objects, routes or controls. Avoid repeating actions that did not advance the task. Missing visual evidence is unknown. Use search to acquire new observations. For confirmed movement_blocked, use a single available jump probe before a recovery route when no jump has been tried; use recover after a jump failed. These are bounded navigation probes whose movement results will be checked. Wait for a pending transition or when observations do not establish whether movement is blocked.",
                    "criteria": {choice: AREA_ACTIONS[choice] for choice in actions},
                },
                "task_kind": {
                    "type": "choice",
                    "instructions": "Classify the requirement stated in the current quest objective.",
                    "criteria": {
                        "follow": "Follow a moving guide or travel to a marked destination.",
                        "search": "Search or investigate within an area.",
                        "interact": "Talk to or interact with a named person or object.",
                        "attack": "Attack or destroy a specified target.",
                        "skill": "Use a specified ability or tool.",
                        "wait": "Wait for an event to complete.",
                        "unknown": "The objective is absent or insufficient to establish a requirement.",
                    },
                },
                "interaction_relevant": {
                    "type": "noul",
                    "instructions": "Does the currently visible F interaction directly advance this specific quest objective? Unnamed, unrelated or absent interactions do not count as relevant.",
                },
            },
        }

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
        confidence = float(action_item["confidence"])
        probabilities = action_item["probabilities"]
        if not 0 <= confidence <= 1 or not isinstance(probabilities, dict) or not probabilities:
            raise ValueError("模型返回的动作置信度或概率无效")
        if any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in probabilities.values()):
            raise ValueError("模型返回的动作概率超出范围")
        if choice not in probabilities or abs(sum(probabilities.values()) - 1) > .05:
            raise ValueError("模型返回的动作概率不完整")
        if probabilities[choice] < max(probabilities.values()):
            raise ValueError("模型返回的选择与动作概率不一致")

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
            "search": ("search", None, "继续搜索任务区域", 0.0, 0),
            "recover": ("recover", None, "执行受阻后的侧向绕行", 0.0, 0),
            "jump": ("jump", "space", "向任务方向跳跃并检查通行结果", 0.0, 0),
            "climb_continue": ("climb_continue", "w", "继续向上攀爬", 0.0, 0),
            "skill": ("skill", None, "使用任务提示中的技能", .5, 0),
        }

        if choice not in action_mapping:
            raise ValueError(f"未知的选择动作: {choice}")

        act_type, act_key, act_desc, act_wait, act_turn = action_mapping[choice]
        usage = response_json.get("usage", {}) if isinstance(response_json, dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
        cost = float(response_json.get("cost", 0.0) or usage.get("cost", 0.0) or 0.0)

        task_kind = answers.get("task_kind", {}).get("choice", "unknown")
        task_confidence = float(answers.get("task_kind", {}).get("confidence", 0))
        if task_kind not in ("unknown", "follow", "search", "interact", "attack", "skill", "wait") or not 0 <= task_confidence <= 1:
            raise ValueError("模型返回的任务类型或置信度无效")
        interaction_relevance = answers.get("interaction_relevant", {}).get("noul")
        if interaction_relevance is not None and not 0 <= interaction_relevance <= 1:
            raise ValueError("模型返回的交互相关性概率无效")

        return QuestAction(
            action_type=act_type,
            key=act_key,
            description=act_desc,
            wait_seconds=act_wait,
            turn_pixels=act_turn,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            confidence=confidence,
            probabilities=probabilities,
            task_kind=task_kind,
            task_confidence=task_confidence,
            interaction_relevance=interaction_relevance,
            called_model=True,
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

    return QuestAction(
        action_type=action_type,
        key=key,
        description=description,
        wait_seconds=wait_seconds,
        turn_pixels=turn_pixels,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost=cost,
        called_model=True,
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
    context: Optional[dict] = None,
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
        context=context,
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
            action = parse_jev_response(resp_dict)
            if context is not None:
                answer_choice = resp_dict["answers"]["action"]["choice"]
                if answer_choice not in context["available_actions"]:
                    raise ValueError("模型返回了当前无法执行的动作")
                if "task_kind" not in resp_dict["answers"] or "interaction_relevant" not in resp_dict["answers"]:
                    raise ValueError("模型返回缺少任务类型或交互相关性")
            return action
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
    context: Optional[dict] = None,
) -> QuestAction:
    if context is None and not is_frozen_letterbox and not is_navigation_guidance:
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
        context=context,
    )


DETOUR_DIRECTIONS = ("left", "right", "back")
DETOUR_SIDE_KEYS = {"left": "a", "right": "d", "back": None}
DEFAULT_CLEF_MODEL = "clef"


def encode_frame_as_data_url(frame: np.ndarray, max_width: int = 1280, jpeg_quality: int = 85) -> str:
    if frame is None or frame.size == 0:
        raise ValueError("输入画面数组不能为空")
    height, width = frame.shape[:2]
    if width > max_width:
        frame = cv2.resize(frame, (max_width, round(height * max_width / width)), interpolation=cv2.INTER_AREA)
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not success:
        raise ValueError("画面编码 JPEG 失败")
    return "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


def build_detour_payload(quest_goal_text: str, context: Optional[dict] = None) -> dict:
    state = sanitize_quest_context({
        "quest_goal": quest_goal_text,
        "situation": "The character is walking toward the quest target but movement is confirmed blocked. "
                     "Look at the game screenshot and judge which side has open ground to walk around the obstacle. "
                     "The camera faces the quest direction: left and right are relative to the screen, "
                     "back means no passable side is visible and the character should retreat first.",
        "observations": context or {},
    })
    return {
        "questions": {
            "direction": {
                "type": "choice",
                "instructions": "Choose the detour direction with the best chance to pass the obstacle based on the screenshot.",
                "criteria": {
                    "left": "The left side of the screen visibly offers open ground or a path around the obstacle.",
                    "right": "The right side of the screen visibly offers open ground or a path around the obstacle.",
                    "back": "Both sides are blocked; retreat backward first and re-approach later.",
                },
            },
        },
        "state": state,
    }


def parse_clef_choice(response_json: dict, question_id: str, valid_choices: tuple) -> dict:
    if not isinstance(response_json, dict):
        raise ValueError("模型返回数据必须为字典格式")
    if response_json.get("success") is False:
        errors = response_json.get("errors")
        raise RuntimeError(f"API 请求失败: {errors}")
    payload = response_json.get("result") if isinstance(response_json.get("result"), dict) else response_json
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("模型返回缺少 answers 字段")
    answer = answers.get(question_id)
    if not isinstance(answer, dict):
        raise ValueError(f"模型返回缺少 {question_id} 字段")
    if answer.get("type") == "noul":
        return {"choice": None, "confidence": 0.0, "probabilities": {}, "total_tokens": 0, "called_model": True}
    choice = answer.get("choice")
    if choice not in valid_choices:
        raise ValueError(f"未知的决策选项: {choice}")
    probabilities = answer.get("probabilities")
    confidence = float(answer.get("confidence", 0) or 0)
    if not isinstance(probabilities, dict) or not 0 <= confidence <= 1:
        raise ValueError("模型返回的置信度或概率无效")
    usage = payload.get("usage", {}) if isinstance(payload.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
    return {
        "choice": choice,
        "confidence": confidence,
        "probabilities": {str(key): float(value) for key, value in probabilities.items()},
        "total_tokens": int(usage.get("total_tokens", input_tokens + output_tokens) or 0),
        "called_model": True,
    }


def parse_detour_response(response_json: dict) -> dict:
    return parse_clef_choice(response_json, "direction", DETOUR_DIRECTIONS)


def parse_approach_response(response_json: dict) -> dict:
    return parse_clef_choice(response_json, "approach", APPROACH_ACTIONS)


def decide_clef_choice(
    frame: np.ndarray,
    question_id: str,
    instructions: str,
    criteria: dict,
    quest_goal_text: str,
    api_url: str,
    api_key: str,
    model: str = DEFAULT_CLEF_MODEL,
    timeout_seconds: float = 12.0,
    context: Optional[dict] = None,
) -> dict:
    endpoint = api_url.strip() if api_url and api_url.strip() else ""
    if not endpoint.startswith("http"):
        raise ValueError("视觉决策 API 端点地址无效")
    if not api_key:
        raise ValueError("视觉决策 API Key 密钥不能为空")
    state = sanitize_quest_context({
        "quest_goal": quest_goal_text,
        "observations": context or {},
    })
    payload = {
        "model": model if model in ("clef", "clef-flash") else DEFAULT_CLEF_MODEL,
        "state": state,
        "questions": {
            question_id: {"type": "choice", "instructions": instructions, "criteria": criteria},
        },
        "images": [encode_frame_as_data_url(frame)],
    }
    req_body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(endpoint, data=req_body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise RuntimeError(f"API 请求失败，HTTP 状态码: {response.status}")
            resp_dict = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as err:
        raise RuntimeError(f"连接视觉决策 API 失败: {err}") from err
    return parse_clef_choice(resp_dict, question_id, tuple(criteria.keys()))


def decide_detour_direction(
    frame: np.ndarray,
    quest_goal_text: str,
    api_url: str,
    api_key: str,
    model: str = DEFAULT_CLEF_MODEL,
    timeout_seconds: float = 12.0,
    context: Optional[dict] = None,
) -> dict:
    criteria = {
        "left": "The left side of the screen visibly offers open ground or a path around the obstacle.",
        "right": "The right side of the screen visibly offers open ground or a path around the obstacle.",
        "back": "Both sides are blocked; retreat backward first and re-approach later.",
    }
    return decide_clef_choice(
        frame,
        "direction",
        "Choose the detour direction with the best chance to pass the obstacle based on the screenshot.",
        criteria,
        quest_goal_text,
        api_url,
        api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        context=context,
    )


APPROACH_ACTIONS = ("walk", "climb", "drop", "detour")


def decide_approach_action(
    frame: np.ndarray,
    quest_goal_text: str,
    api_url: str,
    api_key: str,
    model: str = DEFAULT_CLEF_MODEL,
    timeout_seconds: float = 12.0,
    context: Optional[dict] = None,
) -> dict:
    criteria = {
        "walk": "The target is on walkable ground ahead; keep moving toward it on foot.",
        "climb": "The target is above the character (marker high on screen or up arrow); jump toward it and climb the wall.",
        "drop": "The target is below the character; walk off the nearby edge and drop down to it.",
        "detour": "A wall or obstacle blocks the direct ground path; sidestep around it first.",
    }
    return decide_clef_choice(
        frame,
        "approach",
        "The character is within 30 meters of the quest target and the camera faces it. Choose how to close the remaining distance based on the screenshot.",
        criteria,
        quest_goal_text,
        api_url,
        api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        context=context,
    )
