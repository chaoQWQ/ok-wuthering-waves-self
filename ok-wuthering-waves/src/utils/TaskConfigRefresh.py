from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QFontMetrics

from ok.ui.qt.common.design_system import control_width
from ok.ui.qt.tasks.TaskCard import TaskCard


def refresh_task_config_widgets(task, parent, translate):
    if parent is None:
        return
    for card in parent.findChildren(TaskCard):
        if card.task is not task:
            continue
        widget = card.config_widget_by_key.get('Collection account')
        if widget is not None:
            options = task.config_type['Collection account']['options']
            translated = [translate(option) for option in options]
            combo = widget.combo_box
            # 更新选项时暂停信号，防止清空列表改变当前账号。
            with QSignalBlocker(combo):
                widget.tr_options = translated
                widget.tr_dict = dict(zip(translated, options))
                combo.clear()
                combo.addItems(translated)
                combo.setCurrentIndex(options.index(task.config['Collection account']))
                metrics = QFontMetrics(combo.font())
                combo.setFixedWidth(control_width(max(
                    metrics.horizontalAdvance(text) for text in translated) + 50))
        card.update_config()
