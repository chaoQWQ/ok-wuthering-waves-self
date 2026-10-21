from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, TitleLabel

from ok import Logger, og
from ok.gui.widget.CustomTab import CustomTab
from ok.ui.qt.Communicate import communicate
from ok.ui.qt.tasks.TaskCard import TaskCard
from src.task.MapOverlayTask import MapOverlayTask

logger = Logger.get_logger(__name__)


class MapExplorationTab(CustomTab):
    """独立的地图探索侧边栏标签页。"""

    def __init__(self):
        self._executor = None
        self.card: Optional[TaskCard] = None
        self.task: Optional[MapOverlayTask] = None
        super().__init__()

        self.title_label = TitleLabel(self.tr("Map Exploration"), self.view)
        self.add_widget(self.title_label)

        communicate.task_list_updated.connect(self._on_task_list_updated)
        self._ensure_card()

    @property
    def name(self) -> str:
        return self.tr("Map Exploration")

    @property
    def icon(self) -> FluentIcon:
        return FluentIcon.GLOBE

    @property
    def add_after_default_tabs(self) -> bool:
        return False

    @property
    def executor(self):
        return self._executor or getattr(og, "executor", None)

    @executor.setter
    def executor(self, value):
        self._executor = value
        self._ensure_card()

    def tr(self, message: str) -> str:
        if og.app:
            translated = og.app.tr(message)
            if translated and translated != message:
                return translated
        fallbacks = {
            "Map Exploration": "地图探索",
        }
        return fallbacks.get(message, message)

    def _get_map_task(self) -> Optional[MapOverlayTask]:
        executor = self.executor
        if executor is None:
            return None
        task = self.get_task(MapOverlayTask)
        if task is not None:
            return task
        for trigger_task in getattr(executor, "trigger_tasks", []):
            if isinstance(trigger_task, MapOverlayTask) or getattr(trigger_task, "__class__", None).__name__ == "MapOverlayTask":
                return trigger_task
        return None

    def _ensure_card(self):
        if getattr(self, "card", None) is not None:
            return
        task = self._get_map_task()
        if task is None:
            return
        self.task = task
        self.card = TaskCard(task, False)
        self.card.setExpand(True)
        self.add_widget(self.card)

    def _on_task_list_updated(self):
        task = self._get_map_task()
        if task is not self.task:
            if self.card is not None:
                self.removeWidget(self.card)
                self.card.dispose()
                self.card.deleteLater()
                self.card = None
            self._ensure_card()

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_card()

    def dispose(self):
        if self.card is not None:
            self.card.dispose()
