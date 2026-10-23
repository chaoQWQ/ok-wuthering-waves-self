import uuid

import pytest

from TestGameplayLearning import observer, observations, save_observation
from src.workflow.input import WorkflowStopped
from src.workflow.models import Condition, Step, Workflow
from src.workflow.runner import WorkflowRunner
from src.workflow.store import WorkflowStateStore


@pytest.fixture
def recorded_workflow(tmp_path, observations):
    store = WorkflowStateStore(tmp_path)
    recording = store.start_recording()
    frames = []
    names = ('quest_yangyang_interaction', 'in_combat', 'quest_yangyang_interaction', 'in_combat')
    for index, name in enumerate(names):
        observation = observations[name].model_copy(update={
            'frame_id': uuid.uuid4().hex, 'timestamp': float(index + 1)})
        frames.append(save_observation(store, recording, observation, name))
    conditions = [Condition(scene=frame.scene, objective=frame.objective, texts=[frame.texts[0]]) for frame in frames]
    steps = [Step(step_id=f'combat_{index}', action='combat', preconditions=conditions[index],
                  completion=conditions[index + 1], evidence_before=frames[index].frame_id,
                  evidence_after=frames[index + 1].frame_id,
                  next_step=f'combat_{index + 1}' if index < 2 else None) for index in range(3)]
    workflow = Workflow(title='战斗进度检查', content_id='combat_resume', period='once', game_version='fixture',
                        recording_id=recording, steps=steps)
    store.save_workflow(workflow)
    store.finish_recording(recording, 'complete')
    return store, workflow, frames


@pytest.mark.parametrize('step_index,frame_index,expected', [(2, 2, 2), (0, 1, 1), (2, 3, 3)])
def test_saved_step_resolves_repeated_actual_scene_conditions(recorded_workflow, step_index, frame_index, expected):
    store, workflow, frames = recorded_workflow
    store.checkpoint('account', workflow, workflow.steps[step_index].step_id, 'interrupted',
                     frames[step_index].model_dump())
    with pytest.raises(WorkflowStopped, match='无法唯一确定'):
        WorkflowRunner.locate(workflow, frames[frame_index])
    reopened = WorkflowStateStore(store.root)
    saved = reopened.progress('account', workflow)
    assert WorkflowRunner.locate(workflow, frames[frame_index], saved) == expected


def test_changed_revision_cannot_resume_existing_step(recorded_workflow):
    store, workflow, frames = recorded_workflow
    store.checkpoint('account', workflow, workflow.steps[2].step_id, 'interrupted', frames[2].model_dump())
    changed = workflow.model_copy(update={'title': '战斗进度重新检查'})
    saved = store.progress('account', changed)
    assert saved['revision'] != changed.revision
    with pytest.raises(WorkflowStopped, match='其他流程版本'):
        WorkflowRunner.locate(changed, frames[2], saved)
    assert store.progress('account', workflow) == saved


@pytest.mark.parametrize('step_id', ['missing_step', None])
def test_missing_saved_step_stops_before_input(recorded_workflow, step_id):
    store, workflow, frames = recorded_workflow
    store.checkpoint('account', workflow, step_id, 'interrupted', frames[0].model_dump())
    with pytest.raises(WorkflowStopped, match='步骤不存在|缺少步骤标识'):
        WorkflowRunner.locate(workflow, frames[0], store.progress('account', workflow))


def test_unrelated_actual_scene_cannot_resume_saved_step(recorded_workflow, observations):
    store, workflow, frames = recorded_workflow
    store.checkpoint('account', workflow, workflow.steps[2].step_id, 'interrupted', frames[2].model_dump())
    current = observations['quest_after_story_combat']
    assert not workflow.steps[2].preconditions.matches(current)
    assert not workflow.steps[2].completion.matches(current)
    with pytest.raises(WorkflowStopped, match='无法确认保存步骤'):
        WorkflowRunner.locate(workflow, current, store.progress('account', workflow))


def test_overlapping_saved_step_conditions_require_confirmation(recorded_workflow):
    store, workflow, frames = recorded_workflow
    before = frames[0]
    text = before.texts[0]
    step = Step(step_id='check_overlapping', action='combat', preconditions=Condition(texts=[text]),
                completion=Condition(scene=before.scene, texts=[text]),
                evidence_before=frames[0].frame_id, evidence_after=frames[1].frame_id)
    overlapping = workflow.model_copy(update={'steps': [step]})
    store.checkpoint('account', overlapping, step.step_id, 'interrupted', before.model_dump())
    with pytest.raises(WorkflowStopped, match='无法确认保存步骤'):
        WorkflowRunner.locate(overlapping, before, store.progress('account', overlapping))


def test_completed_progress_remains_completed_across_revisions(recorded_workflow):
    store, workflow, frames = recorded_workflow
    store.checkpoint('account', workflow, None, 'completed', frames[-1].model_dump())
    changed = workflow.model_copy(update={'title': '战斗进度重新检查'})
    saved = store.progress('account', changed)
    assert WorkflowRunner(store, None, None).run('account', changed) == 'completed'
    assert store.progress('account', changed) == saved


def test_unlocated_attention_progress_can_retry_unique_actual_scene(recorded_workflow):
    store, workflow, frames = recorded_workflow
    unique = workflow.model_copy(update={'steps': [workflow.steps[2]]})
    store.checkpoint('account', unique, None, 'needs_attention', frames[2].model_dump())
    assert WorkflowRunner.locate(unique, frames[2], store.progress('account', unique)) == 0
