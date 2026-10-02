import os
import json
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import win32con
import win32gui
import cv2
from PySide6.QtWidgets import QApplication

from config import basic_config_option, config
from ok.util.config import Config
from src.char.Chixia import Chixia
from src.char.Mortefi import Mortefi
from src.char.Verina import Verina
from src.Labels import Labels
from src.char.Hsin import Hsin
from src.char.CharFactory import char_dict
from src.char.BaseChar import CharType, Elements
from src.char.CustomCharLoader import (
    TEAM_CODE_MODE_BUILTIN, TEAM_CODE_MODE_IMPORT,
    clear_team_char_cache, create_custom_team, export_custom_team,
    import_custom_team, inspect_team_archive, read_builtin_char_code,
    read_team_char_code, read_team_import_code, switch_team_code_mode,
    team_code_state,
)
from src.game_launcher import get_game_launch_arguments
from src.gui.CharacterCodeTab import CharacterCodeTab
from src.task.MultiAccountDailyTask import combo_items, combo_selected_item, select_combo_item


class TestUpstreamMergeIntegration(unittest.TestCase):
    def test_hsin_registration_and_feature_images(self):
        registration = char_dict[Labels.char_hsin]
        self.assertIs(registration['cls'], Hsin)
        self.assertEqual(registration['char_type'], CharType.MAIN_DPS)
        self.assertEqual(registration['ring_index'], Elements.ELECTRIC)
        character = Hsin(None, 0, char_name=Labels.char_hsin)
        self.assertFalse(character.lib2_cast_this_turn)
        assets = Path(__file__).resolve().parents[1] / 'assets'
        data = json.loads((assets / 'coco_annotations.json').read_text(encoding='utf-8'))
        images = {image['id']: image for image in data['images']}
        categories = {category['name']: category['id'] for category in data['categories']}
        for label in (Labels.char_hsin, Labels.hsin_h1, Labels.hsin_h2, Labels.hsin_lib1, Labels.hsin_lib2):
            entries = [entry for entry in data['annotations'] if entry['category_id'] == categories[label]]
            self.assertTrue(entries, label)
            for entry in entries:
                image = cv2.imread(str(assets / images[entry['image_id']]['file_name']))
                self.assertIsNotNone(image, label)
                self.assertGreater(image.size, 0)

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

    def test_launcher_config_and_arguments(self):
        self.assertEqual(basic_config_option.default_config['Game Package'], 'hd')
        self.assertEqual(basic_config_option.config_type['Game Package']['options'], ['sd', 'hd', 'uhd'])
        self.assertIn('calculate_pc_exe_path', config['windows'])
        self.assertIs(config['windows']['launch_arguments'], get_game_launch_arguments)


if __name__ == '__main__':
    unittest.main()
