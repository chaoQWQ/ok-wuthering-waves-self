from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem
from qfluentwidgets import BodyLabel, ListWidget, MessageBoxBase, SearchLineEdit, SubtitleLabel

from src.utils.CollectionCatalog import CollectionType


class CollectionTypeDialog(MessageBoxBase):
    def __init__(self, catalog, selected_ids, translate, parent):
        super().__init__(parent)
        self.title_label = SubtitleLabel(translate('Collection types'), self)
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText(translate('Collection types'))
        self.type_list = ListWidget(self)
        self.type_list.setMinimumSize(480, 360)
        self.count_label = BodyLabel(self)
        self.entries = []
        selected = set(selected_ids)
        available = {entry.type_id for entry in catalog}
        # 保留资源包暂时缺少的已选编号，避免更新资源时改变用户选择。
        entries = list(catalog) + [
            CollectionType(type_id, type_id, 0)
            for type_id in sorted(selected - available)
            if type_id != '__no_collection_type__'
        ]
        entries.sort(key=lambda entry: (entry.type_id not in selected, entry.name))
        for entry in entries:
            item = QListWidgetItem(f'{entry.name} ({entry.count})')
            item.setData(Qt.ItemDataRole.UserRole, entry.type_id)
            item.setToolTip(entry.type_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if entry.type_id in selected
                else Qt.CheckState.Unchecked
            )
            self.type_list.addItem(item)
            self.entries.append(item)

        self.viewLayout.setSpacing(12)
        for widget in (self.title_label, self.search_edit, self.type_list, self.count_label):
            self.viewLayout.addWidget(widget)
        self.yesButton.setText(translate('Save'))
        self.cancelButton.setText(translate('Cancel'))
        self.widget.setMinimumWidth(540)
        self.search_edit.textChanged.connect(self.filter_items)
        self.type_list.itemChanged.connect(self.update_count)
        self.update_count()

    def filter_items(self, text):
        query = text.strip().casefold()
        for item in self.entries:
            searchable = item.text() + ' ' + item.data(Qt.ItemDataRole.UserRole)
            item.setHidden(query not in searchable.casefold())

    def selected_type_ids(self):
        return [
            item.data(Qt.ItemDataRole.UserRole) for item in self.entries
            if item.checkState() == Qt.CheckState.Checked
        ]

    def update_count(self, *_args):
        self.count_label.setText(f'{len(self.selected_type_ids())} / {len(self.entries)}')
