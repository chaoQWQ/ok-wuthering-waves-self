from collections import Counter
from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QTableWidgetItem, QVBoxLayout
from qfluentwidgets import BodyLabel, PushButton, TableWidget


class RecordingMaterialsDialog(QDialog):
    def __init__(self, store, identifier, parent=None):
        super().__init__(parent)
        self.materials = store.recording_materials(identifier)
        recording = self.materials['recording']
        timestamp = datetime.fromtimestamp(recording['started']).strftime('%m-%d %H:%M')
        self.setWindowTitle(f'记录材料 — {recording["name"] or timestamp}')
        self.resize(1000, 650)
        layout = QVBoxLayout(self)
        counts = Counter(item['kind'] for item in self.materials['files'])
        self.summary = BodyLabel(
            f'内部片段 {len(self.materials["segments"])} 段，录像 {counts["录像"]} 段，关键截图 {counts["关键截图"]} 张，'
            f'观察记录 {self.materials["observations"]} 条，'
            f'键鼠事件 {self.materials["input_events"]} 条。', self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        hint = BodyLabel('双击文件或点击“打开选中文件”，使用系统应用查看。材料按内部片段顺序排列。', self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if recording['status'] == 'recording':
            hint = BodyLabel('该记录正在录制，录像结束后才能完整播放；数据文件为打开此窗口时的内容。', self)
            hint.setWordWrap(True)
            layout.addWidget(hint)
        self.table = TableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['材料类型', '文件名称', '所在目录'])
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 340)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setRowCount(len(self.materials['files']))
        for row, entry in enumerate(self.materials['files']):
            path = entry['path']
            for column, text in enumerate((entry['kind'], path.name, str(path.parent))):
                cell = QTableWidgetItem(text)
                cell.setToolTip(str(path))
                self.table.setItem(row, column, cell)
            self.table.item(row, 0).setData(Qt.UserRole, str(path))
        self.table.itemDoubleClicked.connect(self.open_selected_file)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        self.open_button = PushButton('打开选中文件', self)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected_file)
        self.table.itemSelectionChanged.connect(self.update_actions)
        buttons.addWidget(self.open_button)
        self.location_button = PushButton('打开文件所在目录', self)
        self.location_button.setEnabled(False)
        self.location_button.clicked.connect(self.open_selected_location)
        buttons.addWidget(self.location_button)
        self.folder_button = PushButton('打开记录材料目录', self)
        self.folder_button.clicked.connect(self.open_recording_directory)
        buttons.addWidget(self.folder_button)
        buttons.addStretch()
        close_button = PushButton('关闭', self)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def update_actions(self):
        selected = bool(self.table.selectionModel().selectedRows())
        self.open_button.setEnabled(selected)
        self.location_button.setEnabled(selected)

    def selected_path(self):
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            raise ValueError('请选择一项材料')
        return self.materials['files'][rows[0].row()]['path']

    @staticmethod
    def open_path(path):
        if not path.exists():
            raise FileNotFoundError(f'材料路径不存在：{path}')
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            raise OSError(f'系统未能打开：{path}')

    def open_selected_file(self, _item=None):
        self.open_path(self.selected_path())

    def open_selected_location(self):
        self.open_path(self.selected_path().parent)

    def open_recording_directory(self):
        self.open_path(self.materials['directory'])
