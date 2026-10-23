import threading
from contextlib import contextmanager


class WorkflowStopped(RuntimeError):
    pass


class InputCoordinator:
    def __init__(self, task, interaction):
        self.task, self.interaction = task, interaction
        self.owner = None
        self.thread_id = threading.get_ident()
        self.last_observation = None

    def observe(self, observation):
        self.last_observation = observation

    def check(self, release=False):
        if threading.get_ident() != self.thread_id:
            raise WorkflowStopped('其他线程不能在流程执行期间发送输入')
        if release:
            return
        if self.owner is None:
            raise WorkflowStopped('当前没有允许发送输入的流程步骤')
        if getattr(self.task.executor.device_manager.interaction, 'coordinator', None) is not self:
            raise WorkflowStopped('游戏输入接口已经变化，停止当前流程')
        if self.task.executor.exit_event.is_set() or self.task.executor.paused or self.task.paused or not self.task.enabled:
            raise WorkflowStopped('流程已经暂停或停止')
        if not self.task.is_game_window_active():
            raise WorkflowStopped('游戏窗口没有位于前台，停止输入')
        self.task.check_workflow_input()

    def release(self):
        keys = set(getattr(self.task, '_held_keys', ())) | set(getattr(self.task, 'held_movement_keys', ()))
        for key in keys:
            self.interaction.send_key_up(key)
        for button in tuple(getattr(self.task, '_held_mouse_buttons', ())):
            self.interaction.mouse_up(key=button)
        self.task._held_keys = set()
        self.task._held_mouse_buttons = set()
        self.task.held_movement_keys = set()

    def move_camera(self, horizontal, vertical=0):
        import win32api
        import win32con
        self.check()
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, int(horizontal), int(vertical), 0, 0)

    def handoff(self, owner):
        self.release()
        self.owner = owner

    @contextmanager
    def control(self, owner):
        if self.owner is not None:
            raise WorkflowStopped('输入控制权尚未释放')
        self.owner = owner
        try:
            yield
        finally:
            try:
                self.release()
            finally:
                self.owner = None


class CoordinatedInteraction:
    INPUT_METHODS = frozenset({
        'click', 'mouse_down', 'mouse_up', 'mouse_move', 'move', 'move_relative',
        'send_key', 'send_key_down', 'send_key_up', 'scroll', 'swipe', 'input_text', 'move_mouse_relative',
    })

    def __init__(self, interaction, coordinator):
        self.original, self.coordinator = interaction, coordinator

    def __getattr__(self, name):
        value = getattr(self.original, name)
        if name not in self.INPUT_METHODS:
            return value

        def execute(*args, **kwargs):
            self.coordinator.check(release=name in ('send_key_up', 'mouse_up'))
            return value(*args, **kwargs)

        return execute


@contextmanager
def coordinated_task(task):
    manager = task.executor.device_manager
    interaction = manager.interaction
    if interaction is None:
        raise WorkflowStopped('游戏输入接口尚未连接')
    coordinator = InputCoordinator(task, interaction)
    enabled = [other for other in task.executor.trigger_tasks if other.enabled]
    for other in enabled:
        other.disable()
    proxy = CoordinatedInteraction(interaction, coordinator)
    manager.interaction = proxy
    try:
        yield coordinator
    finally:
        try:
            coordinator.release()
        finally:
            if manager.interaction is proxy:
                manager.interaction = interaction
            for other in enabled:
                from src.task.BaseCombatTask import BaseCombatTask
                if isinstance(other, BaseCombatTask):
                    other._in_combat = False
                    other.chars = [None, None, None]
                other.enable()
