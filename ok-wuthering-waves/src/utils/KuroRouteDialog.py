from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem, QTextBrowser, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, ListWidget, MessageBoxBase, PushButton, SearchLineEdit, SubtitleLabel

from src.utils.KuroRoutes import KuroRoute, KuroRoutesClient


class RequestSignals(QObject):
    finished = Signal(int, str, object, str)


class RouteRequest(QRunnable):
    def __init__(self, generation, kind, operation):
        super().__init__()
        self.generation = generation
        self.kind = kind
        self.operation = operation
        self.signals = RequestSignals()

    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            self.signals.finished.emit(self.generation, self.kind, None, str(exc))
        else:
            self.signals.finished.emit(self.generation, self.kind, result, '')


class KuroRouteDialog(MessageBoxBase):
    def __init__(self, state_id, translate, parent):
        super().__init__(parent)
        self.client = KuroRoutesClient(state_id)
        self.translate = translate
        self.generation = 0
        self.page = 1
        self.pages = 1
        self.route_data = None
        self.section_id = 0
        self.requests = {}
        self.viewLayout.addWidget(SubtitleLabel(translate('Kuro route square'), self))
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText(translate('Search routes'))
        self.sort_combo = ComboBox(self)
        self.sort_combo.addItem(translate('Most popular'), userData=2)
        self.sort_combo.addItem(translate('Most recent'), userData=1)
        self.sort_combo.setMinimumWidth(125)
        self.scope_combo = ComboBox(self)
        self.scope_combo.addItem(translate('Current map'), userData=False)
        self.scope_combo.addItem(translate('All maps'), userData=True)
        self.scope_combo.setMinimumWidth(140)
        filters = QHBoxLayout()
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.sort_combo)
        filters.addWidget(self.scope_combo)
        self.viewLayout.addLayout(filters)
        self.route_list = ListWidget(self)
        self.route_list.setMinimumHeight(230)
        self.viewLayout.addWidget(self.route_list)
        self.previous_button = PushButton('‹', self)
        self.next_button = PushButton('›', self)
        self.status_label = BodyLabel(self)
        pages = QHBoxLayout()
        pages.addWidget(self.previous_button)
        pages.addWidget(self.status_label, 1)
        pages.addWidget(self.next_button)
        self.viewLayout.addLayout(pages)
        self.instructions = QTextBrowser(self)
        self.instructions.setMinimumHeight(100)
        self.viewLayout.addWidget(self.instructions)
        self.section_combo = ComboBox(self)
        self.viewLayout.addWidget(self.section_combo)
        self.yesButton.setText(translate('Load route'))
        self.yesButton.setEnabled(False)
        self.cancelButton.setText(translate('Cancel'))
        self.widget.setMinimumWidth(750)
        self.search_edit.returnPressed.connect(self.search)
        self.search_edit.searchSignal.connect(self.search)
        self.search_edit.clearSignal.connect(self.search)
        self.sort_combo.currentIndexChanged.connect(self.search)
        self.scope_combo.currentIndexChanged.connect(self.search)
        self.route_list.currentItemChanged.connect(self.select_route)
        self.previous_button.clicked.connect(lambda: self.change_page(-1))
        self.next_button.clicked.connect(lambda: self.change_page(1))
        self.yesButton.clicked.connect(self.save_section)
        self.search()

    def request(self, kind, operation):
        self.generation += 1
        request = RouteRequest(self.generation, kind, operation)
        self.requests[self.generation] = request
        request.signals.finished.connect(self.receive)
        self.status_label.setText('正在加载…')
        self.yesButton.setEnabled(False)
        QThreadPool.globalInstance().start(request)

    def search(self, *_):
        self.page = 1
        self.search_page()

    def change_page(self, delta):
        self.page = max(1, min(self.page + delta, self.pages))
        self.search_page()

    def search_page(self):
        name = self.search_edit.text()
        sort_type = self.sort_combo.currentData()
        all_maps = self.scope_combo.currentData()
        self.sort_combo.setEnabled(not all_maps)
        page = self.page
        self.route_data = None
        self.instructions.clear()
        self.section_combo.clear()
        self.route_list.blockSignals(True)
        self.route_list.clear()
        self.route_list.blockSignals(False)
        self.request('search', lambda: self.client.search(name, sort_type, page, all_maps))

    def select_route(self, item, previous):
        if item is None:
            return
        route_id = item.data(Qt.ItemDataRole.UserRole)
        self.route_data = None
        self.instructions.clear()
        self.section_combo.clear()
        self.request('detail', lambda: self.client.detail(route_id))

    def receive(self, generation, kind, data, error):
        self.requests.pop(generation, None)
        if generation != self.generation:
            return
        if error:
            self.status_label.setText(error)
            return
        if kind == 'search':
            self.pages = max(1, int(data['pages']))
            self.route_list.blockSignals(True)
            for record in data['records']:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, str(record['id']))
                item.setData(Qt.ItemDataRole.UserRole + 1, str(record['name']))
                item.setSizeHint(QSize(650, 72))
                row = QWidget(self.route_list)
                row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                layout = QVBoxLayout(row)
                layout.setContentsMargins(16, 6, 12, 6)
                layout.addWidget(BodyLabel(f"{record['name']}  ·  {record['creatorName']}", row))
                layout.addWidget(BodyLabel(
                    f"浏览 {record['viewCount']}    喜欢 {record['likeCount']}    收藏 {record['favoriteCount']}", row))
                self.route_list.addItem(item)
                self.route_list.setItemWidget(item, row)
            self.route_list.blockSignals(False)
            order_hint = ' · 跨地图结果使用库街区相关性排序' if self.scope_combo.currentData() else ''
            self.status_label.setText(f"{self.page} / {self.pages}    {data['total']}{order_hint}")
            self.previous_button.setEnabled(self.page > 1)
            self.next_button.setEnabled(self.page < self.pages)
        else:
            route = KuroRoute.from_data(data)
            self.route_data = data
            count = sum(len(section.nodes) for section in route.path.sections)
            self.instructions.setPlainText(
                f'{route.name}\n作者：{route.author}\n地图编号：{route.path.state_id}\n'
                f'{route.description}\n\n手动完成探索后使用收集物确认快捷键；推进快捷键跳过当前节点。')
            self.section_combo.addItem(self.translate('All sections'), userData=0)
            for index, section in enumerate(route.path.sections, 1):
                self.section_combo.addItem(f'分段 {index} · {len(section.nodes)} 点', userData=section.section_id)
            self.section_combo.setCurrentIndex(1)
            self.status_label.setText(f'{len(route.path.sections)} 分段 · {count} 点')
            self.yesButton.setEnabled(True)

    def save_section(self):
        self.section_id = int(self.section_combo.currentData() or 0)
