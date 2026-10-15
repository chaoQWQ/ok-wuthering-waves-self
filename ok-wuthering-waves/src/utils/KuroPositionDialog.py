import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout
from qfluentwidgets import BodyLabel, CheckBox, LineEdit, MessageBoxBase, PushButton, SubtitleLabel

from src.utils.PositionSources import PAUSE_SECONDS


class KuroPositionDialog(MessageBoxBase):
    def __init__(self, task, parent, translate=None):
        super().__init__(parent)
        self.task = task
        self.translate = task.tr if translate is None else translate
        self.session_id = ''
        self.viewLayout.addWidget(SubtitleLabel(self.translate('Kuro position settings'), self))
        self.description = BodyLabel(self.translate(
            'Select the folder containing the coordinate and connection JSONL records'), self)
        self.description.setWordWrap(True)
        self.viewLayout.addWidget(self.description)
        self.folder_edit = LineEdit(self)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setText(task.config.get('_Kuro position directory', ''))
        browse = PushButton(self.translate('Select folder'), self)
        browse.clicked.connect(self.choose_folder)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(browse)
        self.viewLayout.addLayout(folder_layout)
        self.account_label = BodyLabel(self)
        self.viewLayout.addWidget(self.account_label)
        self.state_label = BodyLabel(self)
        self.state_label.setWordWrap(True)
        self.viewLayout.addWidget(self.state_label)
        self.confirm_role = CheckBox(self.translate(
            'I verified that the Kuro role and the current game account match this profile'), self)
        self.viewLayout.addWidget(self.confirm_role)
        self.unbind_button = PushButton(self.translate('Unbind session'), self)
        self.unbind_button.clicked.connect(self.unbind)
        self.viewLayout.addWidget(self.unbind_button)
        self.yesButton.setText(self.translate('Bind current session'))
        self.cancelButton.setText(self.translate('Close'))
        self.widget.setMinimumWidth(620)
        self.confirm_role.stateChanged.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.finished.connect(self.timer.stop)
        self.refresh()

    def choose_folder(self):
        directory = QFileDialog.getExistingDirectory(self, self.translate('Select folder'), self.folder_edit.text())
        if directory:
            self.task.config['_Kuro position directory'] = directory
            self.folder_edit.setText(directory)
            self.confirm_role.setChecked(False)
            self.task._configure_position_sources()
            self.refresh()

    def refresh(self, *args):
        self.task._configure_position_sources()
        with self.task._position_lock:
            sources = self.task._position_sources
            now = time.time()
            reader = sources.reader.poll(now) if sources.reader is not None else None
            sample = reader.latest if reader is not None else None
            session_id = sample.session_id if sample is not None else ''
            if self.session_id != session_id:
                self.session_id = session_id
                self.confirm_role.blockSignals(True)
                self.confirm_role.setChecked(False)
                self.confirm_role.blockSignals(False)
            self.account_label.setText(f'{self.translate("Collection account")}: {sources.account}')
            if sample is None:
                status = reader.message if reader is not None else self.translate('Select folder')
            else:
                age = now - sample.received_at
                bound = sources.binding == (sources.account, sample.session_id)
                status = (f'地图 {sample.map_id} · 区域 {sample.area_id}\n'
                          f'X {sample.x / 100:.2f} · Y {sample.y / 100:.2f} · 高度未知\n'
                          f'{age:.1f} 秒前接收 · 实际延迟未知\n'
                          + ('当前会话已绑定' if bound else '当前会话等待绑定'))
            self.state_label.setText(status)
            valid = (reader is not None and reader.connected and sample is not None
                     and 0 <= now - sample.received_at <= PAUSE_SECONDS)
            self.yesButton.setEnabled(valid and self.confirm_role.isChecked())
            self.unbind_button.setEnabled(sources.binding is not None)

    def accept(self):
        self.refresh()
        if not self.yesButton.isEnabled():
            return
        with self.task._position_lock:
            self.task._position_sources.bind(self.session_id)
        self.timer.stop()
        super().accept()

    def reject(self):
        self.timer.stop()
        super().reject()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)

    def unbind(self):
        with self.task._position_lock:
            self.task._position_sources.unbind()
        self.confirm_role.setChecked(False)
        self.refresh()
