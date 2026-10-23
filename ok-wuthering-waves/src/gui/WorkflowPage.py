from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView, QTableWidgetItem
from qfluentwidgets import BodyLabel, CheckBox, FluentIcon, MessageBox, PushButton, TableWidget, TitleLabel

from ok import og
from ok.gui.widget.CustomTab import CustomTab
from src.gui.RecordingNotification import RecordingNotification
from src.task.GameplayLearningTask import GameplayLearningTask
from src.task.MultiAccountWorkflowTask import MultiAccountWorkflowTask
from src.utils.HotkeyWidget import HotkeyLineEdit, LabelAndHotkey
from src.utils.ManualRouteRecorder import HotkeyEdgePoller
from ok.ui.qt.tasks.TaskCard import TaskCard


STATUS_TEXT = {
    'compiled': '已生成', 'offline_checked': '已通过录像检查', 'runtime_verified': '已通过游戏验证',
    'recording': '正在记录', 'complete': '记录完整', 'interrupted': '记录中断', 'error': '记录异常',
    'running': '正在执行', 'completed': '已经完成',
    'needs_attention': '需要处理',
    'materials_deleted': '材料已删除',
}


class WorkflowPage(CustomTab):
    def __init__(self):
        super().__init__()
        self.add_widget(TitleLabel('剧情与活动学习', self.view))
        self.status_label = BodyLabel('启用记录后正常游玩，程序自动整理已完成的内容。', self.view)
        self.status_label.setWordWrap(True)
        self.add_widget(self.status_label)
        self.record_button = PushButton('启用自动记录', self.view)
        self.record_button.clicked.connect(self.toggle_recording)
        self.add_widget(self.record_button)
        self.hotkey_setting = None
        self._hotkey_value = None
        self._hotkey_poller = None
        self._hotkey_ready = False
        self.recording_notification = RecordingNotification()
        self.table = TableWidget(self.view)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['内容', '验证状态', '生成时间'])
        self.table.setMinimumHeight(260)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_widget(self.table)
        self.execute_button = PushButton('执行选中的内容', self.view)
        self.execute_button.clicked.connect(self.execute_selected)
        self.add_widget(self.execute_button)
        self.import_button = PushButton('从多账号日常导入账号配置', self.view)
        self.import_button.clicked.connect(self.import_accounts)
        self.add_widget(self.import_button)
        self.recordings = TableWidget(self.view)
        self.recordings.setColumnCount(4)
        self.recordings.setHorizontalHeaderLabels(['记录名称', '记录时间', '状态', '说明'])
        self.recordings.setColumnWidth(0, 260)
        self.recordings.setColumnWidth(1, 150)
        self.recordings.setColumnWidth(2, 130)
        self.recordings.setMinimumHeight(200)
        self.recordings.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.recordings.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.recordings.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.recordings.horizontalHeader().setStretchLastSection(True)
        self.recordings.itemChanged.connect(self.rename_recording)
        self.add_widget(self.recordings)
        self.add_widget(BodyLabel('双击记录名称可以修改，最多支持 160 个字符。', self.view))
        self.delete_recordings_button = PushButton('删除选中的记录', self.view)
        self.delete_recordings_button.clicked.connect(self.delete_selected_recordings)
        self.recordings.itemSelectionChanged.connect(self.update_recording_actions)
        self.delete_recordings_button.setEnabled(False)
        self.add_widget(self.delete_recordings_button)
        self.progress = TableWidget(self.view)
        self.progress.setColumnCount(4)
        self.progress.setHorizontalHeaderLabels(['账号', '内容', '当前步骤', '状态'])
        self.progress.setMinimumHeight(150)
        self.progress.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.progress.horizontalHeader().setStretchLastSection(True)
        self.add_widget(self.progress)
        self.cards = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.hotkey_timer = QTimer(self)
        self.hotkey_timer.timeout.connect(self.poll_recording_hotkey)
        self.hotkey_timer.start(50)

    @property
    def name(self):
        return '剧情与活动学习'

    @property
    def icon(self):
        return FluentIcon.BOOK_SHELF

    def task(self):
        executor = self.executor or getattr(og, 'executor', None)
        return executor.get_task_by_class(GameplayLearningTask) if executor else None

    def toggle_recording(self, _checked=False, *, notify=False):
        task = self.task()
        if task is None:
            return
        if task.enabled:
            task.disable()
            message = '正在停止录制' if task.recorder is not None else '自动记录已关闭'
        elif task.failure is not None:
            self.status_label.setText('记录服务已经异常停止，请查看任务日志并重新启动程序。')
            message = '无法开启自动记录\n请查看任务日志'
        else:
            task.enable()
            message = '自动记录已启用'
        self.refresh()
        if notify:
            self.recording_notification.show_message(message)

    def suppress_recording_hotkey(self, _value=''):
        # 设置按键时保持停用，直到组合键全部释放。
        self._hotkey_ready = False

    def poll_recording_hotkey(self):
        task = self.task()
        if task is None:
            return
        value = task.config.get('Recording Toggle Hotkey').strip()
        if value != self._hotkey_value:
            self._hotkey_value = value
            self._hotkey_poller = HotkeyEdgePoller(value) if value else None
            self._hotkey_ready = False
            if self._hotkey_poller is not None and not self._hotkey_poller.supported:
                self.hotkey_timer.stop()
                raise ValueError('录制快捷键包含不支持的按键')
        poller = self._hotkey_poller
        if poller is None:
            return
        pressed = poller.poll()
        focus = QApplication.focusWidget()
        if isinstance(focus, HotkeyLineEdit):
            self._hotkey_ready = False
            return
        if not self._hotkey_ready:
            if not any(poller.key_reader(code) for code in poller.vk_codes):
                self._hotkey_ready = True
            return
        if pressed:
            self.toggle_recording(notify=True)

    def execution_task(self):
        executor = self.executor or getattr(og, 'executor', None)
        return executor.get_task_by_class(MultiAccountWorkflowTask) if executor else None

    def execute_selected(self):
        task = self.execution_task()
        if task is None:
            return
        revisions = [self.table.item(index.row(), 0).data(Qt.UserRole)
                     for index in self.table.selectionModel().selectedRows()]
        if not revisions:
            self.status_label.setText('请选择需要执行的内容。')
            return
        learning = self.task()
        workflows = {item['revision']: item for item in learning.store.list_workflows()}
        if any(workflows[revision]['status'] == 'materials_deleted' for revision in revisions):
            dialog = MessageBox('无法执行', '选中的内容包含已删除材料的流程，需要重新录制和学习。', self.window())
            dialog.cancelButton.hide()
            dialog.exec()
            return
        created = {revision: item['created'] for revision, item in workflows.items()}
        revisions.sort(key=created.__getitem__)
        task.config['Workflow revisions'] = revisions
        task._app.start_controller.start(task)

    def import_accounts(self):
        task = self.execution_task()
        if task is not None:
            count = task.import_daily_accounts()
            self.status_label.setText(f'已导入 {count} 个账号的完整身份配置。')

    def rename_recording(self, item):
        if item.column() != 0:
            return
        name = item.text().strip()
        if not name or len(name) > 160:
            dialog = MessageBox('记录名称无效', '请输入 1 至 160 个字符的记录名称。', self.window())
            dialog.cancelButton.hide()
            dialog.exec()
            self.refresh_recordings(self.task().store)
            return
        self.task().store.rename_recording(item.data(Qt.UserRole), name)

    def update_recording_actions(self):
        rows = self.recordings.selectionModel().selectedRows()
        self.delete_recordings_button.setEnabled(bool(rows) and all(
            self.recordings.item(index.row(), 2).data(Qt.UserRole) != 'recording' for index in rows))

    def delete_selected_recordings(self):
        identifiers = [self.recordings.item(index.row(), 0).data(Qt.UserRole)
                       for index in self.recordings.selectionModel().selectedRows()]
        if not identifiers:
            return
        dialog = MessageBox('删除记录', f'确认删除选中的 {len(identifiers)} 条记录？\n'
                            '删除关联材料会清除录像、截图、观察数据和操作记录，无法恢复。\n'
                            '依赖这些材料的生成内容将无法执行。', self.window())
        delete_materials = CheckBox('同时删除关联材料', dialog)
        dialog.textLayout.addWidget(delete_materials)
        activity_hint = BodyLabel('删除材料需要关闭自动记录，并等待分析和执行结束。', dialog)
        activity_hint.setWordWrap(True)
        dialog.textLayout.addWidget(activity_hint)

        def update_materials_option():
            available = self.can_delete_recording_materials()
            delete_materials.setEnabled(available)
            if not available:
                delete_materials.setChecked(False)
            activity_hint.setVisible(not available)

        update_materials_option()
        activity_timer = QTimer(dialog)
        activity_timer.timeout.connect(update_materials_option)
        activity_timer.start(100)
        dialog.yesButton.setText('删除')
        dialog.cancelButton.setText('取消')
        accepted = dialog.exec()
        activity_timer.stop()
        if accepted:
            remove_materials = delete_materials.isChecked()
            if remove_materials and not self.can_delete_recording_materials():
                raise RuntimeError('录制、分析或执行期间无法删除材料')
            self.task().store.delete_recordings(identifiers, delete_materials=remove_materials)
            self.refresh_recordings(self.task().store)
            self.refresh()

    def can_delete_recording_materials(self):
        task = self.task()
        execution = self.execution_task()
        return (not task.enabled and task.recorder is None and not task._analyzing
                and (execution is None or task.executor.current_task is not execution))

    def refresh_recordings(self, store):
        if self.recordings.state() == QAbstractItemView.EditingState:
            return
        selected = {self.recordings.item(index.row(), 0).data(Qt.UserRole)
                    for index in self.recordings.selectionModel().selectedRows()}
        recordings = store.list_recordings()
        self.recordings.blockSignals(True)
        try:
            self.recordings.clearSelection()
            self.recordings.setRowCount(len(recordings))
            for row, item in enumerate(recordings):
                timestamp = datetime.fromtimestamp(item['started']).strftime('%m-%d %H:%M')
                values = (item['name'] or f'记录 {timestamp}', timestamp, STATUS_TEXT[item['status']], item['reason'])
                for column, text in enumerate(values):
                    cell = QTableWidgetItem(text)
                    if column != 0:
                        cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                    cell.setData(Qt.UserRole, item['id'] if column == 0 else item['status'])
                    self.recordings.setItem(row, column, cell)
                    cell.setSelected(item['id'] in selected)
        finally:
            self.recordings.blockSignals(False)
        self.recordings.updateSelectedRows()
        self.update_recording_actions()

    def refresh(self):
        task = self.task()
        if task is None:
            return
        if self.hotkey_setting is None:
            self.hotkey_setting = LabelAndHotkey(task.config_description, task.config, 'Recording Toggle Hotkey',
                                                {'minimum_width': 320})
            self.hotkey_setting.line_edit.hotkey_committed.connect(self.suppress_recording_hotkey)
            self.vBoxLayout.insertWidget(3, self.hotkey_setting)
        if not self.cards and self.execution_task() is not None:
            for entry in (task, self.execution_task()):
                card = TaskCard(entry, isinstance(entry, MultiAccountWorkflowTask))
                self.cards.append(card)
                self.add_widget(card)
                for editor in card.findChildren(HotkeyLineEdit):
                    editor.hotkey_committed.connect(self.suppress_recording_hotkey)
        for setting in self.findChildren(LabelAndHotkey):
            if setting.config is task.config and setting.key == 'Recording Toggle Hotkey' and not setting.line_edit.hasFocus():
                setting.update_value()
        self.record_button.setText('停止自动记录' if task.enabled else '启用自动记录')
        recording_status = task.recorder.status if task.recorder else '未记录'
        if task.failure is not None:
            recording_status = str(task.failure)
        self.status_label.setText(f'{recording_status}；{task.analysis_status}')
        if task.store is None:
            return
        workflows = task.store.list_workflows()
        selected = {self.table.item(index.row(), 0).data(Qt.UserRole)
                    for index in self.table.selectionModel().selectedRows()}
        self.table.setRowCount(len(workflows))
        for row, item in enumerate(workflows):
            values = (item['title'], STATUS_TEXT[item['status']], datetime.fromtimestamp(item['created']).strftime('%m-%d %H:%M'))
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))
            self.table.item(row, 0).setData(Qt.UserRole, item['revision'])
            if item['revision'] in selected:
                for column in range(3):
                    self.table.item(row, column).setSelected(True)
        self.table.updateSelectedRows()
        self.refresh_recordings(task.store)
        progress = task.store.list_progress()
        self.progress.setRowCount(len(progress))
        for row, item in enumerate(progress):
            name = '当前账号' if item['account_name'] == 'current' else item['account_name']
            values = (name, item['title'] or item['content_id'], item['step_id'] or '', STATUS_TEXT[item['status']])
            for column, text in enumerate(values):
                self.progress.setItem(row, column, QTableWidgetItem(text))

    def dispose(self):
        self.timer.stop()
        self.hotkey_timer.stop()
        self.recording_notification.hide_timer.stop()
        self.recording_notification.close()
        for card in self.cards:
            card.dispose()
