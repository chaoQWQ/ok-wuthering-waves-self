import unittest
import tempfile

import config
from ok import og
from ok.util.config import Config
from ok.util.GlobalConfig import GlobalConfig
from src import game_launcher


class TestGameLauncher(unittest.TestCase):
    def setUp(self):
        self.previous_folder = Config.config_folder
        self.previous_global_config = og.global_config
        self.temp_dir = tempfile.TemporaryDirectory()
        Config.config_folder = self.temp_dir.name
        og.global_config = GlobalConfig([config.basic_config_option])

    def tearDown(self):
        og.global_config = self.previous_global_config
        Config.config_folder = self.previous_folder
        self.temp_dir.cleanup()

    def test_windows_config_registers_launch_arguments_callback(self):
        self.assertIs(
            config.config['windows']['launch_arguments'],
            game_launcher.get_game_launch_arguments,
        )

    def test_launch_arguments_read_current_game_package(self):
        basic_options = og.global_config.get_config('Basic Options')
        for package in ('sd', 'hd', 'uhd'):
            basic_options['Game Package'] = package
            self.assertEqual(f'-krqlv={package}', game_launcher.get_game_launch_arguments())

    def test_launch_arguments_default_to_hd(self):
        del og.global_config.get_config('Basic Options')['Game Package']
        self.assertEqual('-krqlv=hd', game_launcher.get_game_launch_arguments())


if __name__ == '__main__':
    unittest.main()
