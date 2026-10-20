import os
import json
import tempfile
import unittest
import time
from copy import deepcopy
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import win32con
import win32gui
import cv2
from PySide6.QtWidgets import QApplication, QWidget

from config import basic_config_option, config
from ok.util.config import Config
from ok.device.DeviceManager import DeviceManager
from ok.device.capture_methods.image import ImageCaptureMethod
from ok.feature.FeatureSet import FeatureSet
from ok.task.TaskExecutor import TaskExecutor
from ok.util.GlobalConfig import GlobalConfig
from ok.util.handler import ExitEvent
from src.char.Chixia import Chixia
from src.char.Mortefi import Mortefi
from src.char.Verina import Verina
from src.Labels import Labels
from src.char.Hsin import Hsin
from src.char.Aemeath import Aemeath
from src.char.Jinhsi import Jinhsi
from src.char.Rover import Rover
from src.char.ShoreKeeper import ShoreKeeper
from src.char.HsinJinhsiShoreKeeper import get_team_rotation
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
from src.gui.CharacterCodeTab import CharacterCodeTab, ImportTeamDialog
from src.scene.WWScene import WWScene
from src.task.FarmEchoTask import FarmEchoTask
from src.task.EnhanceEchoTask import EnhanceEchoTask
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

    def test_import_description_scrolls_without_expanding_dialog(self):
        app = QApplication.instance() or QApplication([])
        manifest = {
            'name': 'Integration', 'version': '1',
            'description': '\n'.join(f'队伍说明 {index}' for index in range(100)),
        }
        parent = QWidget()
        parent.resize(1000, 800)
        dialog = ImportTeamDialog(manifest, 'Mortefi / Chixia / Verina', parent)
        try:
            parent.show()
            dialog.show()
            app.processEvents()
            editor = dialog.description_text
            self.assertTrue(editor.isReadOnly())
            self.assertEqual(editor.toPlainText(), manifest['description'])
            self.assertEqual(editor.height(), 220)
            self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        finally:
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()
            app.processEvents()

    def test_rover_forms_preserve_role_after_reset(self):
        rover = Rover(None, 0, char_name=Labels.char_rover)
        self.assertEqual(rover.char_type, CharType.SUB_DPS)
        rover.ring_index = Elements.ELECTRIC
        self.assertEqual(rover.display_name, 'Rover: Electro')
        self.assertEqual(rover.char_type, CharType.SUB_DPS)
        rover.reset_state()
        self.assertEqual(rover.ring_index, -1)
        self.assertEqual(rover.char_type, CharType.SUB_DPS)
        rover.ring_index = Elements.SPECTRO
        self.assertEqual(rover.display_name, 'Rover: Spectro')
        self.assertEqual(rover.char_type, CharType.MAIN_DPS)


class TestUpstreamScreenshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.previous_folder = Config.config_folder
        cls.temp_dir = tempfile.TemporaryDirectory()
        Config.config_folder = cls.temp_dir.name
        cls.exit_event = ExitEvent()
        cls.global_config = GlobalConfig(config['global_configs'])
        # 仅连接文件中的截图，保持设备输入未连接。
        cls.device = DeviceManager({}, cls.exit_event, cls.global_config)
        cls.device.capture_method = ImageCaptureMethod(cls.exit_event, [])
        matching = config['template_matching']
        cls.features = FeatureSet(
            False, matching['coco_feature_json'],
            matching['default_horizontal_variance'], matching['default_vertical_variance'],
            default_threshold=matching['default_threshold'],
            feature_processor=matching['feature_processor'],
            hcenter_features=matching['hcenter_features'], vcenter_features=matching['vcenter_features'],
        )
        runtime_config = deepcopy(config)
        runtime_config['locale'] = 'zh_CN'
        cls.executor = TaskExecutor(
            cls.device, exit_event=cls.exit_event, feature_set=cls.features,
            global_config=cls.global_config, config=runtime_config, config_folder=cls.temp_dir.name,
        )
        cls.executor.scene = WWScene()
        cls.executor.paused = False
        cls.executor.debug_mode = True
        cls.task = FarmEchoTask(cls.executor, None)
        cls.task.feature_set = cls.features
        cls.task.after_init()

    @classmethod
    def tearDownClass(cls):
        thread = cls.executor._ocr_init_thread
        if thread is not None:
            thread.join(timeout=120)
            if thread.is_alive():
                raise TimeoutError('OCR initialization did not finish within 120 seconds')
        cls.exit_event.set()
        cls.executor.destroy()
        cls.device.close()
        Config.config_folder = cls.previous_folder
        cls.temp_dir.cleanup()

    def set_frame(self, path):
        frame = cv2.imread(str(path))
        self.assertIsNotNone(frame, str(path))
        self.executor.reset_scene()
        self.device.capture_method.set_images([str(path)])
        self.executor._frame = self.device.capture_method.get_frame()
        self.features.check_size(frame)

    def test_health_bar_from_real_combat_screenshot(self):
        self.set_frame('tests/images/in_combat.png')
        self.task._in_combat = False
        self.assertTrue(self.task.check_health_bar())

    def test_zero_level_echo_from_real_bag_screenshot(self):
        self.set_frame('tests/images/echo_enhance.png')
        task = EnhanceEchoTask(self.executor, None)
        task.feature_set = self.features
        task.after_init()
        self.assertTrue(task.is_0_level())
        self.assertTrue(task.find_echo_enhance())

    def test_hsin_heavy_prompts_from_real_feature_images(self):
        data = json.loads(Path('assets/coco_annotations.json').read_text(encoding='utf-8'))
        images = {item['id']: item['file_name'] for item in data['images']}
        categories = {item['name']: item['id'] for item in data['categories']}
        character = Hsin(self.task, 0, char_name=Labels.char_hsin)
        for label, expected in ((Labels.hsin_h1, 1), (Labels.hsin_h2, 2)):
            entry = next(item for item in data['annotations'] if item['category_id'] == categories[label])
            with self.subTest(label=label):
                self.set_frame(Path('assets') / images[entry['image_id']])
                self.assertEqual(character.heavy_available(), expected)

    def test_hsin_reset_preserves_custom_team_entry(self):
        hsin = Hsin(self.task, 0, char_name=Labels.char_hsin)
        self.task.chars = [hsin, Jinhsi(self.task, 1, char_name=Labels.char_jinhsi),
                           ShoreKeeper(self.task, 2, char_name=Labels.char_shorekeeper)]
        rotation = get_team_rotation(hsin)
        self.assertIsNotNone(rotation)
        hsin.finisher_start = time.time()
        hsin.finisher_e_sent = True
        hsin.h2_completed = True
        hsin.h2_guard_until = time.time() + 4
        self.assertTrue(hsin.skip_combat_check())
        hsin.reset_state()
        self.assertFalse(hsin.skip_combat_check())
        self.assertIsNone(hsin.finisher_start)
        self.assertFalse(hsin.finisher_e_sent)
        self.assertFalse(hsin.h2_completed)
        self.assertIsNone(hsin._jinhsi_team_rotation)
        self.assertIsNot(get_team_rotation(hsin), rotation)
        hsin.h2_guard_until = time.time() - 1
        self.assertFalse(hsin.skip_combat_check())

    def test_aemeath_role_decision_uses_real_teammates(self):
        aemeath = Aemeath(self.task, 0)
        self.task.chars = [aemeath, Verina(self.task, 1, char_type=CharType.HEALER),
                           ShoreKeeper(self.task, 2, char_type=CharType.HEALER)]
        self.assertFalse(aemeath.waits_for_intro())
        self.task.chars[1] = Mortefi(self.task, 1, char_type=CharType.SUB_DPS)
        self.assertTrue(aemeath.waits_for_intro())

    def test_farming_config_and_drop_accounting(self):
        self.assertIn('Special Nightmare', self.task.config_type['Teleport to Boss']['options'])
        self.assertEqual(self.task.nightmare_structure, [5, 10])
        self.assertEqual(self.task.config.get('Which Special Nightmare to Teleport'), 1)
        self.task.info.clear()
        self.task.start_time = time.time() - 3600
        self.task.incr_drop(True)
        self.task.incr_drop(False)
        self.task.incr_drop(True)
        self.assertEqual(self.task.info['Echo Count'], 2)
        self.assertEqual(self.task.info['Echo per Hour'], 2)


if __name__ == '__main__':
    unittest.main()
