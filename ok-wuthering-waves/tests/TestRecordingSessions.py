import json
import uuid

import pytest
from PySide6.QtCore import Qt

from TestGameplayLearning import observer, observations, save_observation
from TestGameplayLearningUi import learning_page
from src.gui.RecordingMaterialsDialog import RecordingMaterialsDialog
from src.workflow.models import CompilerResponse, Condition, Step, Workflow
from src.workflow.store import WorkflowStateStore
from src.workflow.validator import WorkflowValidator


def test_session_groups_materials_and_retains_explicit_segment_order(tmp_path, observations):
    store = WorkflowStateStore(tmp_path)
    session = store.start_session()
    children = []
    for name in ('quest_yangyang_interaction', 'quest_after_story_combat'):
        child = store.start_recording(parent_id=session)
        children.append(child)
        store.set_environment(child, 'fixture', 'zh_CN')
        save_observation(store, child, observations[name], name)
        store.finish_recording(child, 'complete')
    assert [item['id'] for item in store.list_recordings()] == [session]
    assert [item['id'] for item in store.session_segments(session)] == children
    assert [item['position'] for item in store.session_segments(session)] == [1, 2]
    assert store.pending_recordings() == []
    store.finish_session(session)
    assert store.pending_recordings() == [session]
    materials = store.recording_materials(session)
    assert materials['observations'] == 2
    assert sum(item['kind'] == '关键截图' for item in materials['files']) == 2
    exported = json.loads(store.resolve(f'{session}/observations.json').read_text(encoding='utf-8'))
    assert [item['frame_id'] for item in exported] == [observations[name].frame_id for name in (
        'quest_yangyang_interaction', 'quest_after_story_combat')]
    assert store.resolve(f'{session}/segments.json').is_file()
    assert store.pending_sessions() == []
    with pytest.raises(ValueError, match='整次录制已经结束'):
        store.start_recording(parent_id=session)


def test_analysis_names_preserve_manual_names_and_survive_reopening(tmp_path):
    store = WorkflowStateStore(tmp_path)
    session = store.start_session()
    response = CompilerResponse(recording_name='活动任务与奖励', workflows=[], unsupported_reasons=['证据不足'])
    store.analysis_name(session, response.recording_name)
    assert store.recording(session)['name'] == '活动任务与奖励'
    reopened = WorkflowStateStore(tmp_path)
    reopened.analysis_name(session, '活动任务')
    assert reopened.recording(session)['name'] == '活动任务'
    reopened.rename_recording(session, '我记录的完整活动')
    reopened.analysis_name(session, '任务领奖')
    assert reopened.recording(session)['name'] == '我记录的完整活动'
    assert reopened.recording(session)['name_source'] == 'manual'


@pytest.mark.parametrize('status,reason', [('complete', '车辆控制尚未支持'), ('interrupted', '截图采集中断')])
def test_session_cannot_execute_with_unsupported_or_interrupted_segment(tmp_path, status, reason):
    store = WorkflowStateStore(tmp_path)
    session = store.start_session()
    child = store.start_recording(session)
    store.set_environment(child, 'fixture', 'zh_CN')
    store.finish_recording(child, status, reason)
    store.analyzed(child, [reason])
    store.finish_session(session)
    store.analyzed(session, [reason])
    assert store.pending_sessions() == [session]
    revisions, explanation = WorkflowValidator(store).session_report(session)
    assert not revisions
    assert reason in explanation
    store.save_session_plan(session, revisions, explanation)
    assert store.list_contents()[0]['status'] == 'needs_attention'
    with pytest.raises(ValueError, match=reason):
        store.execution_workflows([f'recording:{session}'])


def test_session_deletion_includes_all_internal_materials(tmp_path):
    store = WorkflowStateStore(tmp_path)
    session = store.start_session()
    children = [store.start_recording(session) for _ in range(2)]
    for child in children:
        store.set_environment(child, 'fixture', 'zh_CN')
        store.finish_recording(child, 'complete')
    store.finish_session(session)
    store.delete_recordings([session], delete_materials=True)
    assert store.list_recordings() == []
    assert store.pending_recordings() == []
    assert store.list_contents() == []
    assert all(not store.resolve(identifier).exists() for identifier in [session, *children])


def test_learning_toggle_groups_each_session_and_material_dialog_lists_children(learning_page, observations):
    app, page, learning, manager = learning_page
    learning.enable()
    session = learning._recording_session_id
    store = learning.store
    for name in ('quest_yangyang_interaction', 'quest_after_story_combat'):
        child = store.start_recording(session)
        store.set_environment(child, 'fixture', 'zh_CN')
        save_observation(store, child, observations[name], name)
        store.finish_recording(child, 'complete')
    store.analysis_name(session, '秧秧任务')
    learning.disable()
    learning._finish_stopped_sessions()
    page.refresh()
    assert page.recordings.rowCount() == 1
    assert page.table.rowCount() == 1
    assert page.recordings.item(0, 0).text() == '秧秧任务'
    assert page.table.item(0, 0).data(Qt.UserRole) == f'recording:{session}'
    page.recordings.selectRow(0)
    assert not page.execute_recording_button.isEnabled()
    dialog = RecordingMaterialsDialog(store, session, page)
    assert '内部片段 2 段' in dialog.summary.text()
    assert '关键截图 2 张' in dialog.summary.text()
    assert dialog.grab().save(str(store.root.parent / 'grouped-recording-materials.png'))
    dialog.close()
    learning.enable()
    next_session = learning._recording_session_id
    assert next_session != session
    learning.disable()
    learning._finish_stopped_sessions()
    assert len(store.list_recordings()) == 2
    assert manager.interaction is None


@pytest.mark.parametrize('duplicate_identity,cross_segment_action', [(False, False), (True, False), (False, True)])
def test_session_execution_expands_evidence_order_and_checks_progress_identity(
        tmp_path, observations, duplicate_identity, cross_segment_action):
    # 使用真实截图检查离线证据及编排顺序，验证状态保持 offline_checked。
    store = WorkflowStateStore(tmp_path)
    session = store.start_session()
    workflows = []
    pairs = [('quest_yangyang_interaction', 'in_combat'), ('in_combat', 'quest_after_story_combat')]
    for position, names in enumerate(pairs):
        child = store.start_recording(session)
        store.set_environment(child, 'fixture', 'zh_CN')
        frames = []
        for index, name in enumerate(names):
            observation = observations[name].model_copy(update={
                'frame_id': uuid.uuid4().hex, 'timestamp': float(position + index + 1)})
            frames.append(save_observation(store, child, observation, name))
        before, after = frames
        title = next(text for text in before.texts if 2 <= len(text) <= 160 and not text.isdigit())
        preconditions = Condition(scene=before.scene, objective=before.objective, texts=[title])
        completion = Condition(scene=after.scene, objective=after.objective, texts=[after.texts[0]])
        step = Step(step_id='observe_combat', action='combat', preconditions=preconditions,
                    completion=completion, evidence_before=before.frame_id, evidence_after=after.frame_id)
        workflow = Workflow(title=title, content_id='evidence_combat' if duplicate_identity else f'evidence_combat_{position}',
                            period='once', game_version='fixture', recording_id=session, steps=[step])
        workflows.append(workflow)
        store.finish_recording(child, 'complete')
        store.analyzed(child, [])
    store.finish_session(session)
    store.analyzed(session, [])
    if cross_segment_action:
        combined = workflows[0].steps[0].model_copy(update={
            'completion': workflows[-1].steps[-1].completion,
            'evidence_after': workflows[-1].steps[-1].evidence_after})
        workflows = [workflows[0].model_copy(update={'steps': [combined]})]
    for workflow in reversed(workflows):
        WorkflowValidator(store).check_and_save(workflow)
    revisions, reason = WorkflowValidator(store).session_report(session)
    if duplicate_identity:
        assert reason == '前后内容的进度标识重复'
        assert revisions == []
        return
    assert not reason
    assert revisions == [workflow.revision for workflow in workflows]
    store.save_session_plan(session, revisions)
    assert store.execution_workflows([f'recording:{session}']) == workflows
    assert all(row['status'] == 'offline_checked' for row in store.list_workflows())
    store.checkpoint('account', workflows[0], None, 'completed', {})
    if len(workflows) > 1:
        assert store.progress('account', workflows[1]) is None
    with pytest.raises(ValueError, match='请选择整次录制记录执行'):
        store.execution_workflows([workflows[0].revision])
