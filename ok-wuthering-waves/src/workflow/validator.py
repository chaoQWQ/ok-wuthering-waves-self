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
            for frame_id in (step.evidence_before, step.evidence_after, *step.route_frames):
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
