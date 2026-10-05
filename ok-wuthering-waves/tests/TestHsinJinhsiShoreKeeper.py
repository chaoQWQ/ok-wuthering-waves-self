import json
import tempfile
import time
import unittest
from pathlib import Path

from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.Labels import Labels
from src.char.BaseChar import CharType, Elements, SwitchPriority
from src.char.Hsin import Hsin
from src.char.Jinhsi import Jinhsi
from src.char.ShoreKeeper import ShoreKeeper
from src.char.HsinJinhsiShoreKeeper import CYCLE, OPENING, get_team_rotation
from src.task.AutoCombatTask import AutoCombatTask


class TestHsinJinhsiShoreKeeper(TaskTestCase):
    task_class = AutoCombatTask

    @classmethod
    def setUpClass(cls):
        cls.config_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.config_directory.cleanup)
        cls.config = dict(config, config_folder=cls.config_directory.name, disable_file_log=True)
        super().setUpClass()

    def setUp(self):
        self.hsin = Hsin(self.task, 0, char_name=Labels.char_hsin, ring_index=Elements.ELECTRIC)
        self.jinhsi = Jinhsi(self.task, 1, char_name=Labels.char_jinhsi, ring_index=Elements.SPECTRO)
        self.shorekeeper = ShoreKeeper(self.task, 2, char_name=Labels.char_shorekeeper,
                                      char_type=CharType.HEALER, ring_index=Elements.SPECTRO)
        self.task.chars = [self.hsin, self.jinhsi, self.shorekeeper]
        self.rotation = get_team_rotation(self.hsin)

    def test_shared_rotation_and_reset(self):
        self.assertIs(get_team_rotation(self.jinhsi), self.rotation)
        self.assertIs(get_team_rotation(self.shorekeeper), self.rotation)
        self.hsin.reset_state()
        rotation = get_team_rotation(self.jinhsi)
        self.assertIsNot(rotation, self.rotation)
        self.assertTrue(rotation.opening)
        self.assertEqual(rotation.step_index, 0)

    def test_switch_selection_through_opening_and_two_cycles(self):
        characters = {char.char_name: char for char in self.task.chars}
        for steps in (OPENING, CYCLE, CYCLE):
            for step in steps:
                self.assertIs(self.rotation.step, step)
                current = characters[step.character]
                self.rotation.action_index = len(step.actions)
                self.rotation.switch_pending = True
                expected = characters[self.rotation.next_step.character]
                self.shorekeeper.last_full_con_switch_time = time.time()
                for has_intro in (False, True):
                    selected = self.task._choose_switch_target(current, has_intro)
                    self.assertIs(selected, expected)
                intro = self.rotation.next_step.intro
                self.rotation.advance(expected, intro)
                self.assertEqual(self.rotation.action_index, 0)
                self.assertFalse(self.rotation.switch_pending)
        self.assertFalse(self.rotation.opening)
        self.assertIs(self.rotation.step, CYCLE[0])

    def test_reject_unexpected_switch_without_advancing(self):
        self.rotation.switch_pending = True
        with self.assertRaisesRegex(RuntimeError, 'Unexpected rotation target'):
            self.rotation.advance(self.hsin, False)
        self.assertEqual(self.rotation.step_index, 0)
        self.assertTrue(self.rotation.switch_pending)

    def test_reject_missing_intro_without_advancing(self):
        self.rotation.step_index = 3
        self.rotation.switch_pending = True
        with self.assertRaisesRegex(RuntimeError, 'intro missing'):
            self.rotation.advance(self.hsin, False)
        self.assertEqual(self.rotation.step_index, 3)

    def test_initial_switch_returns_to_opening_actor(self):
        self.assertIs(self.task._choose_switch_target(self.hsin, False), self.shorekeeper)
        self.rotation.advance(self.shorekeeper, False)
        self.assertEqual(self.rotation.step_index, 0)

    def test_other_teams_keep_character_priorities(self):
        self.task.chars = [self.hsin, self.jinhsi]
        self.assertIsNone(get_team_rotation(self.hsin))
        self.assertEqual(self.hsin.get_switch_priority(), SwitchPriority.NORMAL)
        self.assertEqual(self.jinhsi.get_switch_priority(has_intro=False), SwitchPriority.NO)
        self.assertEqual(self.jinhsi.get_switch_priority(has_intro=True), SwitchPriority.MUST)
        self.task.chars = [self.hsin, self.shorekeeper]
        self.shorekeeper.last_full_con_switch_time = time.time()
        self.assertTrue(self.shorekeeper.healer_full_con_switch_locked())

    def test_heavy_and_liberation_indicators(self):
        assets = Path('assets')
        data = json.loads((assets / 'coco_annotations.json').read_text(encoding='utf-8'))
        images = {image['id']: image for image in data['images']}
        categories = {category['name']: category['id'] for category in data['categories']}
        character = Hsin(self.task, 0, char_name=Labels.char_hsin)
        for label in (Labels.hsin_h1, Labels.hsin_h2, Labels.hsin_lib1, Labels.hsin_lib2):
            annotations = [entry for entry in data['annotations'] if entry['category_id'] == categories[label]]
            self.assertTrue(annotations)
            for annotation in annotations:
                path = assets / images[annotation['image_id']]['file_name']
                with self.subTest(label=label, image=str(path)):
                    self.set_image(str(path))
                    self.assertIsNotNone(self.task.find_one(label, threshold=0.7))
                    if label in (Labels.hsin_h1, Labels.hsin_h2):
                        self.assertTrue(character.heavy_available())


if __name__ == '__main__':
    unittest.main()
