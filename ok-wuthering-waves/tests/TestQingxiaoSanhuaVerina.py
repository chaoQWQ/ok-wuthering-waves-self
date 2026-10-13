import json
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.Labels import Labels
from src.char.BaseChar import BaseChar, CharType, Elements
from src.char.CustomCharLoader import _load_class_from_file, get_custom_team_folder, inspect_team_archive
from src.char.CharFactory import apply_team_char_classes
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
        phases = [((self.verina, self.sanhua, True), (self.sanhua, self.qingxiao, True),
                   (self.qingxiao, self.sanhua, True))]
        phases.extend([((self.sanhua, self.verina, False), (self.verina, self.sanhua, True),
                        (self.sanhua, self.qingxiao, True), (self.qingxiao, self.sanhua, True))] * 2)
        for phase in phases:
            for current, target, has_intro in phase:
                self.assertEqual(self.rotation.actor, current.char_name)
                self.rotation.switch_pending = True
                self.verina.last_full_con_switch_time = time.time()
                self.assertIs(self.task._choose_switch_target(current, has_intro), target)
                self.rotation.advance(target, has_intro)
        self.assertEqual(self.rotation.round, 3)
        self.qingxiao.reset_state()
        self.assertIsNot(self.qingxiao.team_rotation(), self.rotation)
        self.assertEqual(self.qingxiao.team_rotation().actor, Labels.char_verina)

    def test_partial_liberation_energy_in_recorded_combat(self):
        self.set_image('tests/images/qingxiao_partial_liberation_energy.png')
        self.assertTrue(self.task.load_chars())
        current = self.task.get_current_char()
        self.assertEqual(current.char_name, Labels.char_qingxiao)
        self.assertFalse(current.liberation_available())

    def test_liberation_ready_with_hidden_team_hud(self):
        self.set_image('tests/images/qingxiao_partial_liberation_energy.png')
        self.assertTrue(self.task.load_chars())
        current = self.task.get_current_char()
        self.assertEqual(current.char_name, Labels.char_qingxiao)
        self.set_image('tests/images/qingxiao_liberation_ready_hidden_hud.png')
        self.assertFalse(self.task.in_team()[0])
        self.assertTrue(current.liberation_available())

    def test_wrong_target_stops_and_missing_intro_preserves_actual_state(self):
        self.rotation.switch_pending = True
        with self.assertRaisesRegex(RuntimeError, 'expected'):
            self.rotation.advance(self.qingxiao, True)
        self.assertEqual(self.rotation.step, 0)
        self.assertTrue(self.rotation.switch_pending)
        self.sanhua.has_intro = False
        self.rotation.advance(self.sanhua, False)
        self.assertEqual(self.rotation.actor, Labels.char_sanhua)
        self.assertFalse(self.sanhua.has_intro)
        self.assertFalse(self.rotation.switch_pending)

    def test_sanhua_alternate_portrait_uses_same_rotation(self):
        self.sanhua.char_name = Labels.char_sanhua2
        self.assertIs(self.sanhua.team_rotation(), self.rotation)
        self.rotation.switch_pending = True
        self.assertIs(self.task._choose_switch_target(self.verina, True), self.sanhua)
        self.rotation.advance(self.sanhua, True)
        self.assertEqual(self.rotation.actor, Labels.char_sanhua)

    def test_active_slot_validation_from_combat_image(self):
        self.set_image('tests/images/in_combat.png')
        in_team, current_index, count = self.task.in_team()
        self.assertTrue(in_team)
        self.assertEqual(count, 3)
        current = self.task.chars[current_index]
        self.rotation.ensure_actor(current)
        other = self.task.chars[(current_index + 1) % count]
        with self.assertRaisesRegex(RuntimeError, 'active slot='):
            self.rotation.ensure_actor(other)

    def test_recorded_liberation_cooldown_blocks_duplicate_cast(self):
        self.task.use_liberation = True
        for character in self.task.chars:
            character.record_liberation_use()
            self.assertFalse(self.rotation.liberation_ready(character))
            self.assertFalse(self.rotation.liberation(character))

    def test_live_reload_replaces_rotation_and_preserves_progress(self):
        previous_class = _load_class_from_file(Qingxiao, PACK / 'Qingxiao.py', 'previous_team_qingxiao')
        previous = previous_class(self.task, 1, char_name=Labels.char_qingxiao,
                                  char_type=CharType.MAIN_DPS, ring_index=Elements.WIND)
        self.task.chars[1] = previous
        previous_rotation = previous.team_rotation()
        previous_rotation.step = 1
        previous_rotation.round = 2
        previous_rotation.switch_pending = True
        previous_rotation.field_retries = 2
        previous_rotation.qing_h2_done = True
        folder = get_custom_team_folder(('Qingxiao', 'Sanhua', 'Verina'), create=True)
        for name in ('Qingxiao.py', 'Sanhua.py', 'Verina.py', 'team.json'):
            shutil.copyfile(PACK / name, folder / name)
        apply_team_char_classes(self.task, self.task.chars)
        current = self.task.chars[1]
        rotation = current.team_rotation()
        self.assertIsNot(type(current), previous_class)
        self.assertIsNot(rotation, previous_rotation)
        self.assertEqual(rotation.step, 1)
        self.assertEqual(rotation.round, 2)
        self.assertTrue(rotation.switch_pending)
        self.assertEqual(rotation.field_retries, 2)
        self.assertTrue(rotation.qing_h2_done)
        self.assertEqual(rotation.target, Labels.char_sanhua)

    def test_loop_continues_after_missing_intro(self):
        self.rotation.round = 1
        self.rotation.step = 0
        self.rotation.switch_pending = True
        self.assertTrue(self.rotation.quick_sanhua)
        self.rotation.advance(self.verina, False)
        self.assertEqual(self.rotation.actor, Labels.char_verina)
        self.rotation.switch_pending = True
        self.rotation.advance(self.sanhua, False)
        self.assertFalse(self.rotation.quick_sanhua)
        self.assertEqual(self.rotation.actor, Labels.char_sanhua)

    def test_resource_retries_preserve_heavy_then_resume_verina(self):
        self.rotation.round = 1
        self.rotation.step = 3
        self.rotation.qing_h2_done = True
        for attempt in (1, 2):
            self.rotation.recover(self.qingxiao)
            self.assertEqual(self.rotation.field_retries, attempt)
            self.assertEqual(self.rotation.actor, Labels.char_qingxiao)
            self.assertTrue(self.rotation.qing_h2_done)
        self.rotation.recover(self.qingxiao)
        self.assertEqual(self.rotation.actor, Labels.char_verina)
        self.assertFalse(self.rotation.qing_h2_done)
        self.assertEqual(self.rotation.field_retries, 0)
        self.assertIs(self.task._choose_switch_target(self.qingxiao, False), self.verina)
        self.rotation.advance(self.verina, False)
        self.assertEqual(self.rotation.actor, Labels.char_verina)
        self.rotation.switch_pending = True
        self.rotation.advance(self.sanhua, True)
        self.assertEqual(self.rotation.actor, Labels.char_sanhua)
        self.assertFalse(self.rotation.quick_sanhua)

    def test_confirmed_switch_clears_resource_retries(self):
        self.rotation.field_retries = 2
        self.rotation.qing_h2_done = True
        self.rotation.switch_pending = True
        self.rotation.advance(self.sanhua, True)
        self.assertEqual(self.rotation.field_retries, 0)
        self.assertFalse(self.rotation.qing_h2_done)

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
                        self.assertFalse(self.rotation.resonance_completed(self.qingxiao, enhanced=True))
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
