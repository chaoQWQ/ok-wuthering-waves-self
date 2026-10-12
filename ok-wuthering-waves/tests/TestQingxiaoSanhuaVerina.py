import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.Labels import Labels
from src.char.BaseChar import BaseChar, CharType, Elements
from src.char.CustomCharLoader import _load_class_from_file, inspect_team_archive
from src.char.Qingxiao import Qingxiao
from src.char.Sanhua import Sanhua
from src.char.Verina import Verina
from src.task.AutoCombatTask import AutoCombatTask


PACK = Path('teams/Qingxiao_Sanhua_Verina')


class TestQingxiaoSanhuaVerina(TaskTestCase):
    task_class = AutoCombatTask

    @classmethod
    def setUpClass(cls):
        cls.config_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.config_directory.cleanup)
        cls.config = dict(config, config_folder=cls.config_directory.name, disable_file_log=True)
        super().setUpClass()
        cls.classes = {}
        for builtin in (Qingxiao, Sanhua, Verina):
            custom = _load_class_from_file(builtin, PACK / f'{builtin.__name__}.py',
                                          f'test_team_{builtin.__name__}')
            if custom is builtin or not issubclass(custom, BaseChar):
                raise AssertionError(f'Custom class not loaded: {builtin.__name__}')
            cls.classes[builtin.__name__] = custom

    def setUp(self):
        self.qingxiao = self.classes['Qingxiao'](self.task, 1, char_name=Labels.char_qingxiao,
                                              char_type=CharType.MAIN_DPS, ring_index=Elements.WIND)
        self.sanhua = self.classes['Sanhua'](self.task, 0, char_name=Labels.char_sanhua,
                                          char_type=CharType.SUB_DPS, ring_index=Elements.ICE)
        self.verina = self.classes['Verina'](self.task, 2, char_name=Labels.char_verina,
                                          char_type=CharType.HEALER, ring_index=Elements.SPECTRO)
        self.task.chars = [self.sanhua, self.qingxiao, self.verina]
        self.rotation = self.qingxiao.team_rotation()

    def test_initial_selection_and_continuous_switches(self):
        self.assertIs(self.task._choose_switch_target(self.qingxiao, False), self.verina)
        self.assertIs(self.sanhua.team_rotation(), self.rotation)
        self.assertIs(self.verina.team_rotation(), self.rotation)
        for _ in range(3):
            for current, target in ((self.verina, self.sanhua), (self.sanhua, self.qingxiao),
                                    (self.qingxiao, self.verina)):
                self.assertEqual(self.rotation.actor, current.char_name)
                self.rotation.switch_pending = True
                self.verina.last_full_con_switch_time = time.time()
                self.assertIs(self.task._choose_switch_target(current, True), target)
                self.rotation.advance(target, True)
        self.assertEqual(self.rotation.round, 3)
        self.qingxiao.reset_state()
        self.assertIsNot(self.qingxiao.team_rotation(), self.rotation)
        self.assertEqual(self.qingxiao.team_rotation().actor, Labels.char_verina)

    def test_missing_intro_and_wrong_target_stop_progress(self):
        self.rotation.switch_pending = True
        with self.assertRaisesRegex(RuntimeError, 'requires intro'):
            self.rotation.advance(self.sanhua, False)
        with self.assertRaisesRegex(RuntimeError, 'expected'):
            self.rotation.advance(self.qingxiao, True)
        self.assertEqual(self.rotation.step, 0)
        self.assertTrue(self.rotation.switch_pending)

    def test_sanhua_alternate_portrait_uses_same_rotation(self):
        self.sanhua.char_name = Labels.char_sanhua2
        self.assertIs(self.sanhua.team_rotation(), self.rotation)
        self.rotation.switch_pending = True
        self.assertIs(self.task._choose_switch_target(self.verina, True), self.sanhua)
        self.rotation.advance(self.sanhua, True)
        self.assertEqual(self.rotation.actor, Labels.char_sanhua)

    def test_recorded_liberation_cooldown_blocks_duplicate_cast(self):
        self.task.use_liberation = True
        for character in self.task.chars:
            character.record_liberation_use()
            self.assertFalse(self.rotation.liberation_ready(character))
            self.assertFalse(self.rotation.liberation(character))

    def test_qingxiao_indicators_in_annotated_images(self):
        assets = Path('assets')
        data = json.loads((assets / 'coco_annotations.json').read_text(encoding='utf-8'))
        images = {image['id']: image for image in data['images']}
        categories = {category['name']: category['id'] for category in data['categories']}
        for label in (Labels.qingxiao_h1, Labels.qingxiao_h2, Labels.qingxiao_e):
            annotations = [item for item in data['annotations'] if item['category_id'] == categories[label]]
            self.assertTrue(annotations)
            for annotation in annotations:
                path = assets / images[annotation['image_id']]['file_name']
                with self.subTest(label=label, image=str(path)):
                    self.set_image(str(path))
                    self.assertIsNotNone(self.task.find_one(label, threshold=0.7))
                    if label == Labels.qingxiao_e:
                        self.assertTrue(self.qingxiao.enhanced_resonance_available())
                    else:
                        self.assertTrue(self.qingxiao.heavy_available())

    def test_share_archive_contains_loaded_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / 'team.zip'
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name in ('team.json', 'Qingxiao.py', 'Sanhua.py', 'Verina.py'):
                    archive.write(PACK / name, name)
            info = inspect_team_archive(archive_path)
            self.assertEqual(info['team'], ('Qingxiao', 'Sanhua', 'Verina'))
            for name, code in info['codes'].items():
                self.assertEqual(code, (PACK / f'{name}.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
