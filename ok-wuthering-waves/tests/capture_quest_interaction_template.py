from pathlib import Path

import cv2


root = Path(__file__).resolve().parent.parent
frame = cv2.imread(str(root / "tests/images/quest_yangyang_interaction.png"))
if frame is None:
    raise FileNotFoundError("秧秧交互截图不存在")
key = frame[519:545, 1241:1269]
if not cv2.imwrite(str(root / "assets/interact_f_icon.png"), key):
    raise RuntimeError("交互按键模板保存失败")
