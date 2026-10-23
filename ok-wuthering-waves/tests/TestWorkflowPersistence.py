import ctypes
import json
import os
import uuid
from contextlib import contextmanager
from ctypes import wintypes

import pytest

from TestGameplayLearning import observer, observations, save_observation
from src.workflow.identity import workflow_content_id
from src.workflow.models import Condition, Step, Workflow
from src.workflow.store import WorkflowStateStore


def observed_workflow(store, observations, *, cycle='once', period='once'):
    recording = store.start_recording()
    store.set_environment(recording, 'fixture', 'zh_CN')
    before = save_observation(store, recording, observations['quest_yangyang_interaction'].model_copy(
        update={'frame_id': uuid.uuid4().hex}), 'quest_yangyang_interaction')
    after = save_observation(store, recording, observations['quest_after_story_combat'].model_copy(
        update={'frame_id': uuid.uuid4().hex}), 'quest_after_story_combat')
    step = Step(step_id='observed_transition', action='wait',
                preconditions=Condition(objective=before.objective), completion=Condition(objective=after.objective),
                evidence_before=before.frame_id, evidence_after=after.frame_id)
    workflow = Workflow(title='真实截图条件检查', content_id='existing_content', period=period, cycle=cycle,
                        recording_id=recording, game_version='fixture', steps=[step])
    store.finish_recording(recording, 'complete')
    return workflow, [before, after]


def test_new_recording_reuses_completed_content_without_changing_legacy_progress(tmp_path, observations):
    store = WorkflowStateStore(tmp_path)
    original, original_frames = observed_workflow(store, observations)
    store.save_workflow(original)
    store.checkpoint('account', original, None, 'completed', {})
    current = original.model_copy(update={'content_id': workflow_content_id(original, original_frames)})
    assert store.progress('account', current) is None
    store.save_workflow(current)
    store.checkpoint('account', current, None, 'completed', {})
    repeated, repeated_frames = observed_workflow(store, observations)
    repeated.steps[0].step_id = 'renamed_transition'
    repeated.content_id = workflow_content_id(repeated, repeated_frames)
    assert original.recording_id != repeated.recording_id
    assert current.content_id == repeated.content_id
    assert store.progress('account', repeated)['status'] == 'completed'
    assert store.progress('account', original)['content_id'] == 'existing_content'


def test_identity_distinguishes_period_action_and_repeat_occurrence(tmp_path, observations):
    store = WorkflowStateStore(tmp_path)
    first, frames = observed_workflow(store, observations)
    identity = workflow_content_id(first, frames)
    assert workflow_content_id(first, frames, 1) != identity
    changed = first.model_copy(deep=True)
    changed.steps[0].action = 'navigate'
    assert workflow_content_id(changed, frames) != identity
    changed = first.model_copy(update={'title': '另一项真实记录'})
    assert workflow_content_id(changed, frames) != identity
    event = first.model_copy(update={'cycle': 'event', 'period': 'event_a'})
    later = event.model_copy(update={'period': 'event_b'})
    assert workflow_content_id(event, frames) != workflow_content_id(later, frames)
    daily = first.model_copy(update={'cycle': 'daily', 'period': 'recorded_day_a'})
    next_day = daily.model_copy(update={'period': 'recorded_day_b'})
    assert workflow_content_id(daily, frames) == workflow_content_id(next_day, frames)
    assert workflow_content_id(daily, frames) != identity


@contextmanager
def prevent_file_deletion(path):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows 文件共享删除权限')
@pytest.mark.parametrize('locked_kind', ['screenshot', 'analysis_request'])
def test_real_file_lock_preserves_committed_cleanup_and_recovers_after_release(tmp_path, observations, locked_kind):
    store = WorkflowStateStore(tmp_path)
    workflow, frames = observed_workflow(store, observations)
    store.save_workflow(workflow)
    recording = workflow.recording_id
    locked = store.resolve(frames[-1].screenshot)
    expected = [{'kind': 'recording', 'path': recording}]
    if locked_kind == 'analysis_request':
        store.resolve('agy').mkdir()
        locked = store.resolve(f'agy/{recording}.json')
        locked.write_text(json.dumps({'recording_id': recording, 'observations': [item.model_dump() for item in frames]},
                                    ensure_ascii=False), encoding='utf-8')
        expected.append({'kind': 'analysis', 'path': str(locked.relative_to(store.root))})
    with prevent_file_deletion(locked):
        with pytest.raises(PermissionError):
            store.delete_recordings([recording], delete_materials=True)
        assert store.recording(recording)['deleted'] == 1
        assert store.recording(recording)['status'] == 'materials_deleting'
        assert store.list_recordings() == []
        assert locked.is_file()
        assert store.observations(recording)
        assert store.environment(recording)['language'] == 'zh_CN'
        with store.connect() as db:
            manifest = db.execute('SELECT paths FROM recording_material_cleanup WHERE recording_id=?',
                                  (recording,)).fetchone()
        assert json.loads(manifest['paths']) == expected
        with pytest.raises(ValueError, match='材料不存在'):
            store.save_workflow(workflow)
    reopened = WorkflowStateStore(tmp_path)
    reopened.recover()
    assert reopened.recording(recording)['status'] == 'materials_deleted'
    assert not reopened.resolve(recording).exists()
    assert not reopened.observations(recording)
    with reopened.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM recording_material_cleanup').fetchone()[0] == 0
    reopened.delete_recordings([recording], delete_materials=True)
    reopened.cleanup_recording_materials()
    assert reopened.recording(recording)['status'] == 'materials_deleted'


def test_cleanup_rejects_directory_outside_recording_root(tmp_path):
    store = WorkflowStateStore(tmp_path / 'recordings')
    recording = store.start_recording()
    store.finish_recording(recording, 'complete')
    protected = tmp_path / 'protected'
    protected.mkdir()
    material = protected / 'material.txt'
    material.write_text('需要保留的实际文件', encoding='utf-8')
    with store.connect() as db:
        db.execute('INSERT INTO recording_material_cleanup VALUES(?,?)',
                   (recording, json.dumps([{'kind': 'recording', 'path': '../protected'}])))
    with pytest.raises(ValueError, match='超出记录目录'):
        store.cleanup_recording_materials()
    assert material.read_text(encoding='utf-8') == '需要保留的实际文件'
