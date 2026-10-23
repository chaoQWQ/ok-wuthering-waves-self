import json
from pathlib import Path

import cv2

from config import config
from src.workflow.observer import SceneObserver


def main():
    root = Path(__file__).resolve().parents[1]
    observer = SceneObserver(root, config['template_matching'], {'use_openvino': True, 'use_npu': False})
    frame = cv2.imread(str(root / 'tests/images/quest_yangyang_interaction.png'))
    height, width = frame.shape[:2]
    roi = frame[int(height * .38):int(height * .70), int(width * .55):int(width * .92)]
    result = []
    for scale in (1.0, 1.5, 2.0, 3.0):
        image = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        result.append({'scale': scale, 'texts': [(text, float(confidence))
                                              for _, (text, confidence) in observer.engine.ocr(image, cls=False)[0]]})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
