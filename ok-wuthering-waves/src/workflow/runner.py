import time

from src.workflow.input import WorkflowStopped


class WorkflowRunner:
    def __init__(self, store, task, coordinator):
        self.store, self.task, self.coordinator = store, task, coordinator

    @staticmethod
    def locate(workflow, observation):
        positions = set()
        for index, step in enumerate(workflow.steps):
            if step.preconditions.matches(observation):
                positions.add(index)
            if step.completion.matches(observation):
                positions.add(index + 1)
        if len(positions) != 1:
            raise WorkflowStopped('当前画面无法唯一确定剧情或活动进度')
        return positions.pop()

    def run(self, account, workflow):
        period = workflow.execution_period()
        saved = self.store.progress(account, workflow, period=period)
        if saved is not None and saved['status'] == 'completed':
            return 'completed'
        observation = self.task.observe_workflow()
        self.coordinator.observe(observation)
        try:
            index = self.locate(workflow, observation)
        except WorkflowStopped as error:
            self.store.checkpoint(account, workflow, None, 'needs_attention',
                                  {'observation': observation.model_dump(), 'reason': str(error)}, period=period)
            raise
        if index == len(workflow.steps):
            self.task.sleep(.2)
            confirmed = self.task.observe_workflow()
            if not workflow.steps[-1].completion.matches(confirmed):
                raise WorkflowStopped('任务完成状态未能连续确认')
            self.store.checkpoint(account, workflow, None, 'completed', confirmed.model_dump(), period=period)
            return 'completed'
        attempt = None
        try:
            while index < len(workflow.steps):
                step = workflow.steps[index]
                self.task.info_set('Workflow step', f'{workflow.title} / {step.step_id}')
                self.store.checkpoint(account, workflow, step.step_id, 'running', observation.model_dump(), period=period)
                attempt = self.store.prepare(account, workflow, step)
                deadline = time.monotonic() + step.timeout_seconds
                performed = False
                while True:
                    observation = self.task.observe_workflow()
                    self.coordinator.observe(observation)
                    if step.completion.matches(observation):
                        self.task.sleep(.2)
                        confirmation = self.task.observe_workflow()
                        self.coordinator.observe(confirmation)
                        if step.completion.matches(confirmation):
                            observation = confirmation
                            evidence = self.task.save_workflow_evidence(self.store)
                            if evidence is not None:
                                observation = evidence
                            self.store.finish_attempt(attempt, 'completed', observation.model_dump())
                            attempt = None
                            break
                    if observation.gap or observation.scene in ('unknown', 'login'):
                        raise WorkflowStopped('当前画面不在可执行场景内')
                    if time.monotonic() >= deadline:
                        raise WorkflowStopped(f'步骤 {step.step_id} 未在规定时间内满足完成条件')
                    if not performed and not step.preconditions.matches(observation):
                        raise WorkflowStopped('执行前条件已变化，停止当前步骤')
                    with self.coordinator.control(step.action):
                        self.task.execute_workflow_step(step, observation, performed, deadline)
                    performed = True
                    self.task.sleep(.1)
                index += 1
                following = workflow.steps[index].step_id if index < len(workflow.steps) else None
                self.store.checkpoint(account, workflow, following,
                                      'running' if following else 'completed', observation.model_dump(), period=period)
            self.store.validate(workflow.revision, 'runtime_verified', {
                'account': account, 'final_observation': observation.model_dump(),
                'language': workflow.language, 'game_version': workflow.game_version,
                'resolution': [self.task.width, self.task.height],
            })
            return 'completed'
        except BaseException:
            evidence = self.task.save_workflow_evidence(self.store)
            if evidence is not None:
                observation = evidence
            step_id = workflow.steps[min(index, len(workflow.steps) - 1)].step_id
            self.store.checkpoint(account, workflow, step_id, 'interrupted', observation.model_dump(), period=period)
            if attempt is not None:
                self.store.finish_attempt(attempt, 'interrupted', observation.model_dump())
            raise
