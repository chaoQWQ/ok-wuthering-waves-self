import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit
from qfluentwidgets import BodyLabel, CheckBox, LineEdit, MessageBoxBase, PushButton, SubtitleLabel

from src.utils.PositionSources import PAUSE_SECONDS
from src.utils.KuroDirectClient import KURO_ACCOUNT_3, KURO_ROLE_3, KuroCredentialStore


class KuroPositionDialog(MessageBoxBase):
    def __init__(self, task, parent, translate=None):
        super().__init__(parent)
        self.task = task
        self.translate = task.tr if translate is None else translate
        self.session_id = ''
        self.viewLayout.addWidget(SubtitleLabel(self.translate('Kuro position settings'), self))
        self.token_label = BodyLabel(f'账号 3 · 收集档案 {KURO_ACCOUNT_3} · 游戏 UID {KURO_ROLE_3}', self)
        self.viewLayout.addWidget(self.token_label)
        self.token_edit = LineEdit(self)
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText('输入库街区 token；已保存的内容不会显示')
        self.viewLayout.addWidget(self.token_edit)
        self.confirm_token = CheckBox('已核对该 token 绑定的库街区角色属于账号 3', self)
        self.viewLayout.addWidget(self.confirm_token)
        token_layout = QHBoxLayout()
        self.save_token_button = PushButton('保存账号 3 的 token', self)
        self.save_token_button.clicked.connect(self.save_token)
        token_layout.addWidget(self.save_token_button)
        self.delete_token_button = PushButton('删除已保存 token', self)
        self.delete_token_button.clicked.connect(self.delete_token)
        token_layout.addWidget(self.delete_token_button)
        self.viewLayout.addLayout(token_layout)
        self.token_edit.textChanged.connect(self.refresh)
        self.confirm_token.stateChanged.connect(self.refresh)
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
            account = self.task.config.get('Collection account', 'default')
            self.account_label.setText(f'{self.translate("Collection account")}: {account}')
            if not self.task.enabled:
                status = '地图高价值物品显示未启用；启用后自动连接'
            elif sample is None:
                status = reader.message if reader is not None else sources.kuro_status(now)[1]
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
            direct = sources.reader is not None and getattr(sources.reader, 'direct', False)
            self.yesButton.setEnabled(valid and not direct and self.confirm_role.isChecked())
            self.unbind_button.setEnabled(sources.binding is not None and not direct)
            self.save_token_button.setEnabled(bool(self.token_edit.text().strip())
                                              and self.confirm_token.isChecked())
            self.delete_token_button.setEnabled(self.task._kuro_credential is not None)
            state = '已保存' if self.task._kuro_credential is not None else '未保存'
            self.token_label.setText(
                f'账号 3 · 收集档案 {KURO_ACCOUNT_3} · 游戏 UID {KURO_ROLE_3} · token {state}')

    def save_token(self):
        if not self.save_token_button.isEnabled():
            return
        self.task._kuro_credential = KuroCredentialStore().write(self.token_edit.text())
        self.token_edit.clear()
        self.confirm_token.setChecked(False)
        self.task._configure_position_sources()
        self.refresh()

    def delete_token(self):
        KuroCredentialStore().delete()
        self.task._kuro_credential = None
        self.task._configure_position_sources()
        self.refresh()

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
