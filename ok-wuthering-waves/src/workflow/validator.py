import hashlib
import json

import cv2


class WorkflowValidator:
    def __init__(self, store):
        self.store = store

    def check(self, workflow):
        if self.store.recording(workflow.recording_id)['status'] != 'complete':
            raise ValueError('录制尚未完整结束，不能验证流程')
        environment = self.store.environment(workflow.recording_id)
        if environment != {'game_version': workflow.game_version, 'language': workflow.language}:
            raise ValueError('流程环境与原始记录不符')
        observations = self.store.observations(workflow.recording_id)
        indexed = {item.frame_id: item for item in observations}
        events = self.store.events(workflow.recording_id)
        previous_time = -1.0
        checked = set()
        if workflow.quest_title and not any(workflow.quest_title in item.texts for item in observations):
            raise ValueError('任务名称没有出现在录制材料中')
        if not workflow.quest_title and not any(workflow.title in item.texts for item in observations):
            raise ValueError('内容名称没有出现在录制材料中')
        for step in workflow.steps:
            before = indexed[step.evidence_before]
            after = indexed[step.evidence_after]
            if before.timestamp < previous_time or after.timestamp <= before.timestamp:
                raise ValueError('步骤证据时间顺序不正确')
            if not step.preconditions.matches(before) or not step.completion.matches(after):
                raise ValueError(f'步骤 {step.step_id} 的条件与实际观察记录不符')
            if step.completion.matches(before):
                raise ValueError('完成条件在执行前已经成立，无法证明操作效果')
            interval = [item for item in observations if before.timestamp <= item.timestamp <= after.timestamp]
            if any(item.gap for item in interval):
                raise ValueError('步骤证据区间存在记录中断')
            inputs = [json.loads(item['payload']) for item in events
                      if item['kind'] == 'input' and before.timestamp <= item['timestamp'] <= after.timestamp]
            if step.action in ('interact', 'mechanism') and not any('f' in item['keys'] for item in inputs):
                raise ValueError('交互步骤缺少实际按键证据')
            if step.action in ('select', 'claim') and not any(item['buttons'] for item in inputs):
                raise ValueError('界面操作缺少实际鼠标操作证据')
            if step.action == 'dialog' and not any(item['buttons'] or set(item['keys']) & {'space', 'f'} for item in inputs):
                raise ValueError('对白推进缺少实际操作证据')
            if step.action == 'navigate' and not any(set(item['keys']) & {'w', 'a', 's', 'd'} for item in inputs):
                raise ValueError('导航步骤缺少实际移动证据')
            if step.action == 'wait' and not any(item.scene in ('cutscene', 'loading', 'dialog') or item.timer_visible for item in interval):
                raise ValueError('等待步骤缺少可确认的演出、加载或计时证据')
            if step.action in ('select', 'claim') and sum(control.text == step.target and control.confidence >= .8 for control in before.controls) != 1:
                raise ValueError('点击目标在执行前画面中无法唯一确定')
            if step.action in ('interact', 'mechanism') and step.target != before.interaction:
                raise ValueError('交互对象与实际观察不符')
            if step.action == 'combat' and not any(item.combat for item in interval):
                raise ValueError('战斗步骤缺少实际战斗证据')
            sequence_frames = []
            if step.action == 'input_sequence':
                from src.workflow.playback import InputSequencePlan
                sequence_frames = [item.frame_id for item in InputSequencePlan.load(self.store, workflow, step).checkpoints]
            for frame_id in (step.evidence_before, step.evidence_after, *step.route_frames, *sequence_frames):
                item = indexed[frame_id]
                if not item.screenshot or not item.image_sha256:
                    raise ValueError('步骤缺少可检查的原始截图')
                data = self.store.resolve(item.screenshot).read_bytes()
                if hashlib.sha256(data).hexdigest() != item.image_sha256:
                    raise ValueError('截图已经变化，无法继续使用原有验证结果')
                if cv2.imread(str(self.store.resolve(item.screenshot))) is None:
                    raise ValueError('步骤截图无法解码')
                checked.add(frame_id)
            previous_time = after.timestamp
        return {'recording_id': workflow.recording_id, 'frames': sorted(checked), 'steps': len(workflow.steps)}

    def check_and_save(self, workflow):
        evidence = self.check(workflow)
        self.store.save_workflow(workflow)
        self.store.validate(workflow.revision, 'offline_checked', evidence)

    @staticmethod
    def no_action_needed(observations, events):
        if not observations or any(item['kind'] == 'input' for item in events):
            return False
        first = observations[0]
        identity = first.scene, first.objective, first.interaction, first.texts
        return all(not item.gap and not item.combat and not item.timer_visible
                   and item.scene in ('world', 'menu', 'result')
                   and (item.scene, item.objective, item.interaction, item.texts) == identity
                   for item in observations)

    def can_skip_recording(self, identifier):
        if not self.no_action_needed(self.store.observations(identifier), self.store.events(identifier)):
            return False
        if self.store.is_session(identifier):
            return all(self.can_skip_recording(item['id']) for item in self.store.session_segments(identifier))
        diagnostic = self.store.resolve(f'{identifier}/diagnostics.json')
        if diagnostic.is_file():
            summary = json.loads(diagnostic.read_text(encoding='utf-8'))
            return summary['inputs']['received'] == 0
        return not self.store.recording(identifier)['parent_id']

    def session_report(self, identifier):
        recording = self.store.recording(identifier)
        if recording['status'] != 'complete':
            return [], recording['reason'] or '整次录制尚未完整结束'
        segments = self.store.session_segments(identifier)
        if not segments:
            return [], '整次录制没有取得游玩材料'
        for segment in segments:
            if segment['status'] != 'complete' or segment['deleted']:
                return [], f'第 {segment["position"]} 段：' + (segment['reason'] or '片段尚未完整保存')
        if recording['reason'] or not recording['analyzed']:
            return [], recording['reason'] or '整次录制尚未完成分析'
        observations = self.store.observations(identifier)
        if any(item.gap for item in observations):
            return [], '完整观察材料存在缺口'
        frames = [item for item in observations if item.screenshot]
        if not frames:
            return [], '完整录制没有关键画面'
        indexed = {item.frame_id: item for item in observations}
        rows = self.store.list_workflows(identifier)
        workflows = [self.store.workflow(row['revision'], executable=False) for row in rows]
        states = {row['revision']: row['status'] for row in rows}
        workflows.sort(key=lambda item: indexed[item.steps[0].evidence_before].timestamp)
        if not workflows:
            return [], '整次录制没有需要复现的操作'
        if not workflows[0].steps[0].preconditions.matches(frames[0]):
            return [], '生成步骤没有覆盖整次录制的起始状态'
        if not workflows[-1].steps[-1].completion.matches(frames[-1]):
            return [], '生成步骤没有覆盖整次录制的结束状态'
        revisions, identities = [], set()
        previous, completion = None, None
        for workflow in workflows:
            if states[workflow.revision] not in ('offline_checked', 'runtime_verified'):
                return [], '完整流程尚未通过录像检查'
            identity = workflow.content_id, workflow.execution_period()
            if identity in identities:
                return [], '前后内容的进度标识重复'
            identities.add(identity)
            self.check(workflow)
            for earlier, later in zip(workflow.steps, workflow.steps[1:]):
                if (not earlier.completion.matches(indexed[later.evidence_before])
                        or not later.preconditions.matches(indexed[earlier.evidence_after])):
                    return [], '相邻步骤的观察状态无法连续连接'
            before = indexed[workflow.steps[0].evidence_before]
            if previous is not None and (not workflow.steps[0].preconditions.matches(previous)
                                         or not completion.matches(before)):
                return [], '前后内容的观察状态无法连续连接'
            previous = indexed[workflow.steps[-1].evidence_after]
            completion = workflow.steps[-1].completion
            revisions.append(workflow.revision)
        return revisions, ''

    def session_revisions(self, identifier):
        revisions, reason = self.session_report(identifier)
        if reason:
            raise ValueError(reason)
        return revisions
