import argparse
import json
import threading
import time
from pathlib import Path

import cv2

from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame
from src.workflow.compiler import AgyVisionProvider
from src.workflow.models import CompilerResponse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / 'working_images' / f'workflow_agy_check_{time.time_ns()}'
    output.mkdir(parents=True)
    payload = {
        'recording_id': 'independent_screenshot_check', 'game_version': 'fixture', 'language': 'zh_CN',
        'source_description': '两张独立的真实截图，没有连续游玩记录，也没有操作或任务完成证据。',
        'observations': [], 'events': [], 'schema': CompilerResponse.model_json_schema(),
    }
    images = []
    if arguments.request is not None:
        payload = json.loads(arguments.request.read_text(encoding='utf-8'))
        images = [(item['frame_id'], Path(item['path'])) for item in payload.pop('images')]
        if not images or any(not path.is_file() for _, path in images):
            raise ValueError('分析请求中的截图文件缺失')
    else:
        for name in ('quest_yangyang_interaction', 'quest_after_story_combat'):
            frame = cv2.imread(str(root / 'tests/images' / f'{name}.png'))
            if frame is None:
                raise FileNotFoundError(name)
            path = output / f'{name}.png'
            if not cv2.imwrite(str(path), prepare_quest_ocr_frame(frame)):
                raise RuntimeError('真实截图验证材料保存失败')
            images.append((name, path))
    provider = AgyVisionProvider(output, threading.Event())
    response = provider.compile(payload, images)
    if arguments.request is None and (response.workflows or not response.unsupported_reasons):
        raise ValueError('缺少连续证据时，AGY 不应生成可执行流程')
    result = output / 'verification.json'
    result.write_text(response.model_dump_json(indent=2), encoding='utf-8')
    print(json.dumps({'verified': True, 'result': str(result)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
