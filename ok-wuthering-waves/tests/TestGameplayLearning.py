import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import pytest

from config import config
from src.workflow.models import Condition, Step, Workflow, cycle_period
from src.workflow.observer import IdleTracker, SceneObserver, protect_observation_frame
from src.workflow.recorder import persist_inputs
from src.workflow.store import WorkflowStateStore
from src.workflow.validator import WorkflowValidator
from src.workflow.video import SegmentedVideoWriter, read_video


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def observer():
    return SceneObserver(ROOT, config['template_matching'], {'use_openvino': True, 'use_npu': False})


@pytest.fixture(scope='module')
def observations(observer):
    result = {}
    for index, name in enumerate(('quest_yangyang_interaction', 'quest_after_story_combat', 'in_combat')):
        frame = cv2.imread(str(ROOT / 'tests/images' / f'{name}.png'))
        assert frame is not None
        result[name] = observer.observe(frame, float(index + 1), True)
    return result


def test_actual_npc_screen_recognizes_goal_and_target(observations):
    observation = observations['quest_yangyang_interaction']
    assert '秧秧' in observation.objective
    assert observation.interaction == '秧秧', ascii(observation.interaction)
    assert observation.scene == 'world'
    assert not observation.combat


def test_actual_combat_screen_is_not_idle(observations):
    observation = observations['in_combat']
    assert observation.combat
    idle = IdleTracker()
    assert not idle.observe(observation.model_copy(update={'timestamp': 50.0, 'changed': False}))


def test_afk_policy_on_repeated_actual_game_frame(observer):
    frame = cv2.imread(str(ROOT / 'tests/images/quest_yangyang_interaction.png'))
    first = observer.observe(frame, 0.0, True)
    repeated = observer.observe(frame, 31.0, True)
    assert not repeated.changed
    tracker = IdleTracker()
    assert not tracker.observe(first)
    assert not tracker.observe(repeated)
    assert tracker.observe(repeated.model_copy(update={'timestamp': 62.0}))
    assert not tracker.observe(repeated.model_copy(update={'timestamp': 63.0}), input_active=True)
    assert not tracker.observe(repeated.model_copy(update={'timestamp': 100.0, 'timer_visible': True}))


def save_observation(store, recording, observation, name):
    frame = cv2.imread(str(ROOT / 'tests/images' / f'{name}.png'))
    path = store.resolve(f'{recording}/{observation.frame_id}.png')
    assert cv2.imwrite(str(path), frame)
    observation = observation.model_copy(update={
        'screenshot': str(path.relative_to(store.root)),
        'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    store.observe(recording, observation)
    return observation


def test_screenshot_only_recipe_is_not_executable_and_progress_persists(tmp_path, observations):
    store = WorkflowStateStore(tmp_path)
    recording = store.start_recording()
    store.set_environment(recording, 'fixture', 'zh_CN')
    before = save_observation(store, recording, observations['quest_yangyang_interaction'], 'quest_yangyang_interaction')
    after = save_observation(store, recording, observations['quest_after_story_combat'], 'quest_after_story_combat')
    assert before.objective != after.objective
    step = Step(step_id='observed_transition', action='wait',
                preconditions=Condition(objective=before.objective), completion=Condition(objective=after.objective),
                evidence_before=before.frame_id, evidence_after=after.frame_id)
    workflow = Workflow(title='真实截图条件检查', content_id='screenshot_validation', period='once',
                        recording_id=recording, game_version='fixture', steps=[step])
    store.finish_recording(recording, 'complete')
    with pytest.raises(ValueError):
        WorkflowValidator(store).check_and_save(workflow)
    store.save_workflow(workflow)
    assert store.workflow(workflow.revision, executable=False) == workflow
    with pytest.raises(ValueError):
        store.workflow(workflow.revision)
    store.checkpoint('account', workflow, step.step_id, 'running', before.model_dump())
    attempt = store.prepare('account', workflow, step)
    store.recover()
    assert store.progress('account', workflow)['status'] == 'interrupted'
    with store.connect() as db:
        assert db.execute('SELECT status FROM step_attempts WHERE id=?', (attempt,)).fetchone()[0] == 'interrupted'
    store.checkpoint('account', workflow, None, 'completed', after.model_dump())
    store.checkpoint('account', workflow, step.step_id, 'interrupted', before.model_dump())
    assert store.progress('account', workflow)['status'] == 'completed'
    with pytest.raises(ValueError):
        store.resolve('../outside.png')


def test_codec_roundtrip_preserves_capture_timing(tmp_path):
    frame = cv2.imread(str(ROOT / 'tests/images/quest_yangyang_interaction.png'))
    writer = SegmentedVideoWriter(tmp_path, segment_seconds=.1)
    try:
        for index in range(5):
            writer.write(frame, 10.0 + index * .05)
    finally:
        writer.close()
    decoded = [entry for path in sorted(tmp_path.glob('*.mkv')) for entry in read_video(path)]
    assert len(decoded) == 5
    assert [entry[0] for entry in decoded] == pytest.approx([10.0 + index * .05 for index in range(5)], abs=.002)
    assert decoded[0][1].shape == (frame.shape[0] - frame.shape[0] % 2, frame.shape[1] - frame.shape[1] % 2, 3)


def test_combat_input_filter_uses_actual_scene_classification(tmp_path, observations):
    store = WorkflowStateStore(tmp_path)
    recording = store.start_recording()
    before = observations['quest_yangyang_interaction'].model_copy(update={'timestamp': 0.0})
    combat = observations['in_combat'].model_copy(update={'timestamp': 1.0})
    after = observations['quest_after_story_combat'].model_copy(update={'timestamp': 2.0})
    persist_inputs(store, recording, [(.5, {'keys': ['w']})], [before, combat, after])
    assert not store.events(recording)


def test_daily_and_weekly_cycles_use_beijing_four_am():
    zone = timezone(timedelta(hours=8))
    before = datetime(2026, 10, 26, 3, 59, tzinfo=zone)
    after = datetime(2026, 10, 26, 4, 0, tzinfo=zone)
    assert cycle_period('daily', '', before) == 'daily:2026-10-25'
    assert cycle_period('daily', '', after) == 'daily:2026-10-26'
    assert cycle_period('weekly', '', before) == 'weekly:2026-10-19'
    assert cycle_period('weekly', '', after) == 'weekly:2026-10-26'
    assert cycle_period('once', '', after) == 'once'
    assert cycle_period('event', 'anniversary', after) == 'event:anniversary'


def test_actual_logged_out_screen_contains_no_learning_text(observer):
    frame = cv2.imread(str(ROOT / 'tests/images/account_logged_out_title.png'))
    observation = observer.observe(frame, 150.0, True)
    assert observation.scene == 'login'
    assert not observation.texts
    assert not observation.controls
    assert not observation.objective


def test_actual_game_frame_redacts_profile_region(observations):
    frame = cv2.imread(str(ROOT / 'tests/images/quest_yangyang_interaction.png'))
    observation = observations['quest_yangyang_interaction']
    protected = protect_observation_frame(frame, observation)
    height, width = frame.shape[:2]
    assert frame[int(height * .9):, int(width * .7):].any()
    assert not protected[int(height * .9):, int(width * .7):].any()
    assert protected[:height // 2, :width // 2].any()
