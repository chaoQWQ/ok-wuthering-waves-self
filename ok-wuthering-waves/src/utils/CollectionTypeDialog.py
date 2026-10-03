from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem
from qfluentwidgets import BodyLabel, ComboBox, ListWidget, MessageBoxBase, SearchLineEdit, SubtitleLabel

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
        self.populate_catalog(catalog, selected_ids)

        self.viewLayout.setSpacing(12)
        for widget in (self.title_label, self.search_edit, self.type_list, self.count_label):
            self.viewLayout.addWidget(widget)
        self.yesButton.setText(translate('Save'))
        self.cancelButton.setText(translate('Cancel'))
        self.widget.setMinimumWidth(540)
        self.search_edit.textChanged.connect(self.filter_items)
        self.type_list.itemChanged.connect(self.update_count)
        self.update_count()

    def populate_catalog(self, catalog, selected_ids, show_unavailable=True):
        self.type_list.blockSignals(True)
        self.type_list.clear()
        self.entries = []
        selected = set(selected_ids)
        available = {entry.type_id for entry in catalog}
        # 保留资源包暂时缺少的已选编号，避免更新资源时改变用户选择。
        entries = list(catalog) + [
            CollectionType(type_id, type_id, 0)
            for type_id in sorted(selected - available)
            if type_id != '__no_collection_type__' and show_unavailable
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

        self.type_list.blockSignals(False)
        self.filter_items(self.search_edit.text())
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


class MapCollectionTypeDialog(CollectionTypeDialog):
    def __init__(self, maps, default_ids, selections, initial_key, translate, parent):
        if not maps:
            raise ValueError('地图资源没有可用的地区和地图')
        super().__init__([], [], translate, parent)
        self.maps = {entry.key: entry for entry in maps}
        self.default_ids = set(default_ids) - {'__no_collection_type__'}
        self.selections = {key: list(value) for key, value in selections.items()}
        self.current_key = None
        self.region_combo = ComboBox(self)
        self.map_combo = ComboBox(self)
        regions = {entry.country_id: entry.country_name for entry in maps}
        for country_id, name in regions.items():
            self.region_combo.addItem(name, userData=country_id)
        self.viewLayout.insertWidget(1, BodyLabel(translate('Collection region'), self))
        self.viewLayout.insertWidget(2, self.region_combo)
        self.viewLayout.insertWidget(3, BodyLabel(translate('Collection map'), self))
        self.viewLayout.insertWidget(4, self.map_combo)
        self.type_list.setMinimumHeight(280)
        initial = self.maps.get(initial_key, maps[0])
        self.region_combo.setCurrentIndex(self.region_combo.findData(initial.country_id))
        self._load_region(initial.key)
        self.region_combo.currentIndexChanged.connect(lambda *_: self._load_region())
        self.map_combo.currentIndexChanged.connect(self._load_map)

    def _remember_current(self):
        if self.current_key is None:
            return
        available = {entry.type_id for entry in self.maps[self.current_key].types}
        unavailable = set(self.selections.get(self.current_key, [])) - available
        self.selections[self.current_key] = sorted(unavailable | set(self.selected_type_ids()))

    def _load_region(self, initial_key=None):
        self._remember_current()
        self.current_key = None
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        country_id = self.region_combo.currentData()
        for entry in self.maps.values():
            if entry.country_id == country_id:
                # 大世界共用同一幅底图，地区名称由资源包提供。
                name = entry.country_name if entry.state_id == 8 else entry.state_name
                self.map_combo.addItem(name, userData=entry.key)
        index = self.map_combo.findData(initial_key)
        self.map_combo.setCurrentIndex(max(0, index))
        self.map_combo.blockSignals(False)
        self._load_map()

    def _load_map(self, *_args):
        self._remember_current()
        self.current_key = self.map_combo.currentData()
        entry = self.maps[self.current_key]
        selected = self.selections.get(entry.key, self.default_ids)
        self.populate_catalog(entry.types, selected, show_unavailable=False)

    def selected_map_types(self):
        self._remember_current()
        return {key: list(value) for key, value in self.selections.items()}
