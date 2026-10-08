import re
import unicodedata

import numpy as np
from zhconv import convert


_IDENTITY_LABEL = re.compile(r"特\s*征\s*[码碼]|(?<![A-Za-z])U\s*I\s*D(?![A-Za-z])", re.IGNORECASE)
_IDENTITY_NUMBER = re.compile(r"(?<![A-Za-z0-9])[0-9OoIl](?:[ \t-]*[0-9OoIl]){7,}(?![A-Za-z0-9])")
_NUMBER_ONLY = re.compile(r"[0-9OoIl \t-]{4,}")


def prepare_quest_ocr_frame(frame: np.ndarray) -> np.ndarray:
    if frame is None or frame.size == 0:
        raise ValueError("OCR 画面不能为空")
    protected_frame = frame.copy()
    height, width = protected_frame.shape[:2]
    # 在 OCR 识别之前遮蔽右下角特征码区域。
    protected_frame[int(height * 0.90):, int(width * 0.70):] = 0
    return protected_frame


def sanitize_quest_text(text: str) -> str:
    clean_lines = []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        # 游戏内部分 NPC 名与 UI 文案以繁体渲染（如"煙舒"），而任务指引是简体，
        # 统一归一为简体后两侧文本才能可靠匹配。
        line = convert(line, "zh-cn")
        if _IDENTITY_LABEL.search(line):
            continue
        line = _IDENTITY_NUMBER.sub("", line).strip()
        if line and not _NUMBER_ONLY.fullmatch(line):
            clean_lines.append(line)
    return "\n".join(clean_lines)


OPTIONAL_QUEST_MARKER = re.compile(
    r"[（(【\[]\s*可(?:\s*[选選])?|"
    r"[选選]\s*[）)】\]]|"
    r"可[选選]|"
    r"\boptional\b",
    re.IGNORECASE,
)


def quest_goal_from_lines(lines: list[str]) -> str:
    distance = re.compile(r"\s*\d+(?:\.\d+)?\s*(?:米|m|M)\s*[▲△▼▽↑↓]*\s*")
    goal_lines = []
    for line in lines:
        raw_clean = sanitize_quest_text(line or "").strip(" -—•·▲△▼▽↑↓")
        if not raw_clean or OPTIONAL_QUEST_MARKER.search(raw_clean):
            continue
        line = re.sub(r"^[A-Za-z]\s+(?=[\u4e00-\u9fff])", "", raw_clean)
        line = re.sub(r"[（(]?\d+\s*[/／]\s*\d+[）)]?", "", line).strip()
        if len(line) >= 2 and not distance.fullmatch(line):
            goal_lines.append(line)
    return " ".join(goal_lines)


def is_named_quest_interaction(goal: str, interaction: str) -> bool:
    goal = sanitize_quest_text(goal)
    interaction = sanitize_quest_text(interaction).strip()
    if not 2 <= len(interaction) <= 32 or re.search(r"不要|禁止|避免|do not|avoid", goal, re.IGNORECASE):
        return False

    clean_name = re.sub(r"[^\w\s\u4e00-\u9fff]", "", interaction).strip()
    if len(clean_name) >= 2 and clean_name in goal:
        return True

    name = re.escape(interaction)
    chinese = rf"(?:与|和|向|寻找|找|跟随)\s*{name}\s*(?:对话|對話|交谈|交談|汇合|匯合|会合|會合|见面|見面)?"
    english = rf"(?:meet|reunite with|talk to|speak to|speak with|find|look for|follow)\s+{name}(?!\w)"
    return bool(re.search(chinese, goal) or re.search(english, goal, re.IGNORECASE))
