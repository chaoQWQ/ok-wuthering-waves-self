import os
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import win32con
import win32gui
from PySide6.QtWidgets import QApplication

from config import basic_config_option, config
from ok.util.config import Config
from src.char.Chixia import Chixia
from src.char.Mortefi import Mortefi
from src.char.Verina import Verina
from src.char.CustomCharLoader import (
    TEAM_CODE_MODE_BUILTIN, TEAM_CODE_MODE_IMPORT,
    clear_team_char_cache, create_custom_team, export_custom_team,
    import_custom_team, inspect_team_archive, read_builtin_char_code,
    read_team_char_code, read_team_import_code, switch_team_code_mode,
    team_code_state,
)
from src.game_launcher import execute_game, install_game_launcher
from src.gui.CharacterCodeTab import CharacterCodeTab
from src.task.MultiAccountDailyTask import combo_items, combo_selected_item, select_combo_item


class TestUpstreamMergeIntegration(unittest.TestCase):
    def test_native_combo_account_selection(self):
        parent = win32gui.CreateWindow('STATIC', 'Account selection test', 0,
                                      0, 0, 320, 200, 0, 0, 0, None)
        try:
            combo = win32gui.CreateWindow('COMBOBOX', '', win32con.WS_CHILD | win32con.CBS_DROPDOWNLIST,
                                         0, 0, 300, 180, parent, 1, 0, None)
            accounts = ['aa****01@example.com', 'bb****02@example.com', '普通账号']
            for account in accounts:
                self.assertGreaterEqual(win32gui.SendMessage(combo, win32con.CB_ADDSTRING, 0, account), 0)
            self.assertEqual(combo_items(combo), accounts)
            self.assertIsNone(combo_selected_item(combo))
            for index, account in enumerate(accounts):
                self.assertTrue(select_combo_item(combo, index))
                self.assertEqual(combo_selected_item(combo), account)
        finally:
            win32gui.DestroyWindow(parent)

    def test_team_import_switch_and_editor(self):
        app = QApplication.instance() or QApplication([])
        previous = Config.config_folder
        with tempfile.TemporaryDirectory() as folder:
            Config.config_folder = folder
            clear_team_char_cache()
            tab = None
            try:
                team = (Mortefi, Chixia, Verina)
                create_custom_team(team)
                archive = export_custom_team(team, folder, 'Integration', '内置角色代码验证', 'Integration', '1')
                import_custom_team(inspect_team_archive(archive))
                self.assertEqual(team_code_state(team), TEAM_CODE_MODE_IMPORT)
                for mode in (TEAM_CODE_MODE_BUILTIN, TEAM_CODE_MODE_IMPORT):
                    switch_team_code_mode(team, mode)
                    self.assertEqual(team_code_state(team), mode)
                    for char_cls in team:
                        self.assertEqual(read_team_char_code(team, char_cls), read_builtin_char_code(char_cls))
                        self.assertEqual(read_team_import_code(team, char_cls), read_builtin_char_code(char_cls))
                tab = CharacterCodeTab()
                app.processEvents()
                self.assertTrue(tab.switch_all_button.isEnabled())
            finally:
                if tab is not None:
                    tab.deleteLater()
                    app.processEvents()
                clear_team_char_cache()
                Config.config_folder = previous

    def test_launcher_config_and_adapter(self):
        from ok.core import start_controller

        self.assertEqual(basic_config_option.default_config['Game Package'], 'hd')
        self.assertEqual(basic_config_option.config_type['Game Package']['options'], ['sd', 'hd', 'uhd'])
        self.assertIn('calculate_pc_exe_path', config['windows'])
        original = start_controller.execute
        try:
            install_game_launcher()
            self.assertIs(start_controller.execute, execute_game)
        finally:
            start_controller.execute = original


if __name__ == '__main__':
    unittest.main()
