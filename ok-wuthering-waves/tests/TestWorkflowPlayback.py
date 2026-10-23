import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import pytest

from src.workflow.input import WorkflowStopped
from src.workflow.playback import InputSequencePlayer, check_sample


ROOT = Path(__file__).resolve().parents[1]


def test_existing_native_capture_accepts_tool_key_and_requires_text_action_for_click():
    evidence = json.loads((ROOT / 'tests/data/workflow_input_capture.json').read_text(encoding='utf-8'))
    assert evidence['source'] == '真实 Qt 验证窗口的 Windows Raw Input'
    accepted, rejected = set(), 0
    for item in evidence['samples']:
        sample = item['payload']
        key = sample.get('key_event', {}).get('key')
        if key in ('tab', 't'):
            check_sample(sample)
            accepted.add(key)
        if 'left' in sample['buttons'] and 'tab' not in sample['keys']:
            with pytest.raises(ValueError, match='必须生成独立文字交互步骤'):
                check_sample(sample)
            rejected += 1
    assert accepted == {'t'}
    assert rejected == 1


def test_sequence_image_guard_accepts_actual_same_scene_and_rejects_other_location():
    before = cv2.imread(str(ROOT / 'tests/images/quest_yangyang_interaction.png'))
    after = cv2.imread(str(ROOT / 'tests/images/quest_after_story_combat.png'))
    assert before is not None and after is not None
    InputSequencePlayer._check_reference(before, before)
    with pytest.raises(WorkflowStopped, match='位置或视角'):
        InputSequencePlayer._check_reference(before, after)


@pytest.mark.skipif(os.name != 'nt' or os.environ.get('RUN_NATIVE_WORKFLOW_PLAYBACK') != '1',
                    reason='需要启用独立 Windows 窗口的真实输入验证')
def test_real_windows_playback_and_stop_release(tmp_path):
    environment = dict(os.environ, QT_QPA_PLATFORM='windows', PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, str(ROOT / 'tools/verify_workflow_playback.py'),
                             '--output-dir', str(tmp_path)], cwd=ROOT, env=environment,
                            text=True, encoding='utf-8', capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / 'verification.json').read_text(encoding='utf-8'))
    assert report['verified']
    assert report['normal_release_verified'] and report['stop_release_verified']
    assert report['watchdog_release_verified']
    assert .1 <= report['watchdog_release_seconds'] < .4
    assert report['relative_motion'] == [48, 16]
    assert report['maximum_cursor_error_pixels'] <= 1
