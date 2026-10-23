import json
import time
from pathlib import Path

import cv2

from config import config
from ok import OK
from src.task.MultiAccountWorkflowTask import MultiAccountWorkflowTask
from src.workflow.accounts import AccountSessionController
from src.workflow.input import coordinated_task
from src.workflow.models import Account
from src.workflow.observer import protect_observation_frame
from src.workflow.store import WorkflowStateStore


class VerifyLearnedWorkflowTask(MultiAccountWorkflowTask):
    def run(self):
        self.verified = False
        store = WorkflowStateStore(self.output)
        with coordinated_task(self) as coordinator:
            self._coordinator = coordinator
            try:
                with coordinator.control('account'):
                    self.ensure_in_front()
                    self.ensure_main(time_out=180)
                    session = AccountSessionController(self.executor, self._app, [], store)
                    session.config = self.config
                    code = session.identify()
                profile = Account(login_name='current', region='current', profile_code=code)
                store.save_account(profile)
                observation = self.observe_workflow()
                if observation.scene != 'world':
                    raise RuntimeError('账号确认后没有回到可识别的世界界面')
                screenshot = store.root / 'world.png'
                if not cv2.imwrite(str(screenshot), protect_observation_frame(self.frame, observation)):
                    raise RuntimeError('真实游戏验证画面保存失败')
                self.report = {'account_identity': profile.identity, 'scene': observation.scene,
                               'profile_open_and_close_verified': True, 'quest_or_event_executed': False,
                               'screenshot': str(screenshot)}
                (store.root / 'verification.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')
                self.verified = True
            finally:
                self._coordinator = None


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'working_images' / f'workflow_execution_check_{time.time_ns()}'
    output.mkdir(parents=True)
    runtime_config = dict(config)
    runtime_config.update({'gui': None, 'custom_tasks': False, 'disable_file_log': True, 'locale': 'zh_CN',
                           'onetime_tasks': [], 'trigger_tasks': []})
    runtime = OK(runtime_config)
    try:
        task = VerifyLearnedWorkflowTask(executor=runtime.task_executor, app=runtime.headless_app)
        task.after_init(executor=runtime.task_executor, scene=runtime.task_executor.scene)
        task.output = output
        task.verified = False
        runtime.task_executor.onetime_tasks.append(task)
        runtime.run_task(task)
        if not task.verified:
            raise RuntimeError('真实游戏身份核查和输入管理验证未完成，请查看执行日志')
        print(json.dumps(task.report, ensure_ascii=False))
    finally:
        runtime.quit()


if __name__ == '__main__':
    main()
