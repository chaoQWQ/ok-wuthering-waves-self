from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem
from qfluentwidgets import (
    BodyLabel, LineEdit, ListWidget, MessageBox, MessageBoxBase,
    PushButton, SearchLineEdit, SubtitleLabel,
)

from src.utils.MapMarksDB import DEFAULT_ACCOUNT_ID, MapMarksDB


class CollectionAccountManager(MessageBoxBase):
    def __init__(self, db_path, current_account, translate, on_change, parent):
        super().__init__(parent)
        self.db_path = db_path
        self.backup_directory = str(Path(db_path).parent / 'account_backups')
        self.current_account = current_account
        self.translate = translate
        self.on_change = on_change
        self.viewLayout.addWidget(SubtitleLabel(translate('Collection account management'), self))
        description = BodyLabel(translate('Local profiles only; select the matching profile after changing the game account'), self)
        description.setWordWrap(True)
        self.viewLayout.addWidget(description)
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText(translate('Search accounts'))
        self.viewLayout.addWidget(self.search_edit)
        self.account_list = ListWidget(self)
        self.account_list.setMinimumHeight(230)
        self.viewLayout.addWidget(self.account_list)
        self.name_edit = LineEdit(self)
        self.name_edit.setMaxLength(64)
        self.name_edit.setPlaceholderText(translate('Game UID or account alias'))
        self.viewLayout.addWidget(self.name_edit)
        buttons = QHBoxLayout()
        self.add_button = PushButton(translate('Add account'), self)
        self.rename_button = PushButton(translate('Rename account'), self)
        self.delete_button = PushButton(translate('Delete account'), self)
        for button in (self.add_button, self.rename_button, self.delete_button):
            buttons.addWidget(button)
        self.viewLayout.addLayout(buttons)
        self.status_label = BodyLabel(self)
        self.status_label.setWordWrap(True)
        self.viewLayout.addWidget(self.status_label)
        self.yesButton.setText(translate('Use selected account'))
        self.cancelButton.setText(translate('Close'))
        self.widget.setMinimumWidth(570)
        self.search_edit.textChanged.connect(self.filter_accounts)
        self.account_list.currentItemChanged.connect(self.selection_changed)
        self.name_edit.textChanged.connect(self.update_buttons)
        self.add_button.clicked.connect(self.add_account)
        self.rename_button.clicked.connect(self.rename_account)
        self.delete_button.clicked.connect(self.confirm_delete)
        self.yesButton.clicked.connect(self.use_selected)
        self.reload(current_account)

    def database(self):
        return MapMarksDB(self.db_path)

    def selected_account(self):
        item = self.account_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def reload(self, selected):
        db = self.database()
        try:
            self.accounts = db.list_accounts()
            counts = db.account_counts()
        finally:
            db.close()
        self.account_list.clear()
        for account in self.accounts:
            label = self.translate('Default profile') if account == DEFAULT_ACCOUNT_ID else account
            active = self.translate('Current profile') if account == self.current_account else ''
            item = QListWidgetItem(f'{label}  ·  {counts[account]} {self.translate("Completed marks")}  {active}')
            item.setData(Qt.UserRole, account)
            self.account_list.addItem(item)
            if account == selected:
                self.account_list.setCurrentItem(item)
        self.filter_accounts(self.search_edit.text())
        self.update_buttons()

    def filter_accounts(self, text):
        for index in range(self.account_list.count()):
            item = self.account_list.item(index)
            item.setHidden(str(text).casefold() not in str(item.data(Qt.UserRole)).casefold())
        self.update_buttons()

    def selection_changed(self, current, previous):
        self.name_edit.setText(current.data(Qt.UserRole) if current is not None else '')
        self.update_buttons()

    def update_buttons(self, *args):
        name = self.name_edit.text().strip()
        selected = self.selected_account()
        exists = name in getattr(self, 'accounts', [])
        self.add_button.setEnabled(bool(name) and not exists)
        item = self.account_list.currentItem()
        visible = item is not None and not item.isHidden()
        editable = visible and selected != DEFAULT_ACCOUNT_ID
        self.rename_button.setEnabled(editable and bool(name) and not exists)
        self.delete_button.setEnabled(editable)
        self.yesButton.setEnabled(visible)

    def apply_current(self, account):
        self.on_change(account)
        self.current_account = account

    def add_account(self):
        db = self.database()
        try:
            account = db.create_account(self.name_edit.text())
        finally:
            db.close()
        self.search_edit.clear()
        self.apply_current(account)
        self.reload(account)

    def rename_account(self):
        old = self.selected_account()
        db = self.database()
        try:
            name = db.rename_account(old, self.name_edit.text(), self.backup_directory)
        finally:
            db.close()
        self.apply_current(name if self.current_account == old else self.current_account)
        self.reload(name)

    def confirm_delete(self):
        account = self.selected_account()
        text = self.translate('Delete this profile and its completion records? A database backup will be saved first.')
        dialog = MessageBox(self.translate('Delete account'), f'{account}\n\n{text}', self)
        if dialog.exec():
            self.delete_account(account)

    def delete_account(self, account):
        db = self.database()
        try:
            backup_path = db.delete_account(account, self.backup_directory)
        finally:
            db.close()
        self.apply_current(DEFAULT_ACCOUNT_ID if self.current_account == account else self.current_account)
        self.reload(self.current_account)
        self.status_label.setText(self.translate('Backup saved') + ': ' + backup_path)

    def use_selected(self):
        selected = self.selected_account()
        if selected is not None:
            self.apply_current(selected)
