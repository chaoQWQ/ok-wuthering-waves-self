from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem
from qfluentwidgets import BodyLabel, ComboBox, ListWidget, MessageBoxBase, SearchLineEdit, SubtitleLabel

from src.utils.CollectionCatalog import CollectionType


class CollectionTypeDialog(MessageBoxBase):
    def __init__(self, catalog, selected_ids, translate, parent):
        super().__init__(parent)
        self.translate = translate
        self.title_label = SubtitleLabel(translate('Collection types'), self)
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText(translate('Collection types'))
        self.type_list = ListWidget(self)
        self.type_list.setMinimumSize(350, 360)
        self.category_list = ListWidget(self)
        self.category_list.setFixedWidth(170)
        self.category_entries = {}
        self.category_names = {}
        self.count_label = BodyLabel(self)
        self.entries = []
        self.populate_catalog(catalog, selected_ids)

        self.viewLayout.setSpacing(12)
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.search_edit)
        category_layout = QHBoxLayout()
        category_layout.addWidget(self.category_list)
        category_layout.addWidget(self.type_list, 1)
        self.viewLayout.addLayout(category_layout)
        self.viewLayout.addWidget(self.count_label)
        self.yesButton.setText(translate('Save'))
        self.cancelButton.setText(translate('Cancel'))
        self.widget.setMinimumWidth(620)
        self.search_edit.textChanged.connect(self.filter_items)
        self.category_list.currentRowChanged.connect(
            lambda *_: self.filter_items(self.search_edit.text()))
        self.type_list.itemChanged.connect(self.update_count)
        self.update_count()

    def populate_catalog(self, catalog, selected_ids, show_unavailable=True):
        current_category = self.category_list.currentItem()
        current_id = current_category.data(Qt.ItemDataRole.UserRole) if current_category else '3'
        self.category_list.blockSignals(True)
        self.category_list.clear()
        self.category_entries = {}
        self.category_names = {}
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
        entries.sort(key=lambda entry: (entry.category_order, entry.type_order, entry.name))
        for entry in entries:
            item = QListWidgetItem(f'{entry.name} ({entry.count})')
            item.setData(Qt.ItemDataRole.UserRole, entry.type_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.category_id)
            item.setToolTip(entry.type_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if entry.type_id in selected
                else Qt.CheckState.Unchecked
            )
            self.type_list.addItem(item)
            self.entries.append(item)
            if entry.category_id not in self.category_entries:
                category_item = QListWidgetItem()
                category_item.setData(Qt.ItemDataRole.UserRole, entry.category_id)
                self.category_list.addItem(category_item)
                self.category_entries[entry.category_id] = category_item
                self.category_names[entry.category_id] = self.translate(entry.category_name)

        self.type_list.blockSignals(False)
        selected_category = self.category_entries.get(current_id)
        if selected_category is None:
            selected_category = self.category_entries.get('3')
        if selected_category is not None:
            self.category_list.setCurrentItem(selected_category)
        else:
            self.category_list.setCurrentRow(0)
        self.category_list.blockSignals(False)
        self.filter_items(self.search_edit.text())
        self.update_count()

    def filter_items(self, text):
        query = text.strip().casefold()
        category_item = self.category_list.currentItem()
        category_id = category_item.data(Qt.ItemDataRole.UserRole) if category_item else None
        for item in self.entries:
            searchable = item.text() + ' ' + item.data(Qt.ItemDataRole.UserRole)
            item.setHidden(item.data(Qt.ItemDataRole.UserRole + 1) != category_id
                           or query not in searchable.casefold())

    def selected_type_ids(self):
        return [
            item.data(Qt.ItemDataRole.UserRole) for item in self.entries
            if item.checkState() == Qt.CheckState.Checked
        ]

    def update_count(self, *_args):
        self.count_label.setText(f'{len(self.selected_type_ids())} / {len(self.entries)}')
        for category_id, category_item in self.category_entries.items():
            items = [item for item in self.entries
                     if item.data(Qt.ItemDataRole.UserRole + 1) == category_id]
            checked = sum(item.checkState() == Qt.CheckState.Checked for item in items)
            category_item.setText(f'{self.category_names[category_id]}  {checked}/{len(items)}')


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
