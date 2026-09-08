"""Fluent dialog used to add map-collection completion profiles."""

from PySide6.QtWidgets import QApplication
from qfluentwidgets import BodyLabel, LineEdit, MessageBoxBase, SubtitleLabel

from ok import og


MAX_ACCOUNT_ID_LENGTH = 64


def _tr(text):
    try:
        return og.app.tr(text)
    except Exception:  # pragma: no cover - app is unavailable in headless runs
        return text


class CollectionAccountDialog(MessageBoxBase):
    """Ask for a game UID or a user-defined local profile alias."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title_label = SubtitleLabel(_tr('Add collection account'), self)
        self.description_label = BodyLabel(
            _tr('Enter a game UID or custom account alias'), self
        )
        self.account_edit = LineEdit(self)
        self.account_edit.setMaxLength(MAX_ACCOUNT_ID_LENGTH)
        self.account_edit.setPlaceholderText(_tr('Game UID or account alias'))
        self.account_edit.textChanged.connect(self._update_confirm_enabled)

        self.viewLayout.setSpacing(12)
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.description_label)
        self.viewLayout.addWidget(self.account_edit)
        self.yesButton.setText(_tr('Add'))
        self.cancelButton.setText(_tr('Cancel'))
        self.widget.setMinimumWidth(420)
        self._update_confirm_enabled('')

    def _update_confirm_enabled(self, text):
        self.yesButton.setEnabled(bool(str(text).strip()))

    def account_id(self):
        return self.account_edit.text().strip()


def prompt_collection_account(parent=None):
    """Return a new profile id, or ``None`` when the dialog is cancelled."""
    if QApplication.instance() is None or parent is None:
        return None
    dialog = CollectionAccountDialog(parent)
    if not dialog.exec():
        return None
    return dialog.account_id() or None
