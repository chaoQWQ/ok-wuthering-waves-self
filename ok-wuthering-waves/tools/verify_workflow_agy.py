import argparse
import hashlib
import json
import threading
import time
from pathlib import Path

import cv2

from src.utils.QuestOcrPrivacy import prepare_quest_ocr_frame
from config import config
from src.workflow.compiler import AgyVisionProvider, WorkflowCompiler
from src.workflow.models import CompilerResponse
from src.workflow.observer import SceneObserver
from src.workflow.store import WorkflowStateStore
from src.workflow.validator import WorkflowValidator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path)
    parser.add_argument('--session', action='store_true')
    arguments = parser.parse_args()
    if arguments.session and arguments.request is not None:
        raise ValueError('整次录制验证使用自带的独立真实截图材料')
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
    if arguments.session:
        store = WorkflowStateStore(output)
        session = store.start_session()
        observer = SceneObserver(root, config['template_matching'], {'use_openvino': True, 'use_npu': False})
        for position, (_, path) in enumerate(images):
            child = store.start_recording(session)
            store.set_environment(child, 'fixture', 'zh_CN')
            observation = observer.observe(cv2.imread(str(path)), float(position + 1), True)
            observation.screenshot = path.name
            observation.image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            store.observe(child, observation)
            store.finish_recording(child, 'complete')
        store.event(session, 0.0, 'source_description', {'text': payload['source_description']})
        store.finish_session(session)
        workflows = WorkflowCompiler(store, provider).compile(session, **store.environment(session))
        recording = store.recording(session)
        if workflows or not recording['reason'] or not recording['name']:
            raise AssertionError('独立截图缺少完整证据时，应保留 AGY 名称和无法执行的原因')
        revisions, reason = WorkflowValidator(store).session_report(session)
        store.save_session_plan(session, revisions, reason)
        report = {'verified': True, 'source': payload['source_description'], 'recording': recording,
                  'segments': len(store.session_segments(session)), 'plan': store.session_plan(session)}
        result = output / 'verification.json'
        result.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'verified': True, 'name': recording['name'], 'result': str(result)}, ensure_ascii=False))
        return
    response = provider.compile(payload, images)
    if arguments.request is None and (response.workflows or not response.unsupported_reasons):
        raise ValueError('缺少连续证据时，AGY 不应生成可执行流程')
    result = output / 'verification.json'
    result.write_text(response.model_dump_json(indent=2), encoding='utf-8')
    print(json.dumps({'verified': True, 'result': str(result)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
