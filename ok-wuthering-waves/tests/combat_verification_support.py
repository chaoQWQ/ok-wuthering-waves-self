import re

import cv2

from src.task.MultiAccountDailyTask import MultiAccountDailyTask, normalize_profile_code
from src.task.NightmareNestTask import TRAVEL_FEATURES


class BossVerificationPreparation:
    def heal_team(self):
        self.send_key('m', after_sleep=2)
        controls = self.ocr(.6, .7, 1, 1, match='切换地图')
        if len(controls) != 1:
            raise RuntimeError('地图没有显示切换地图按钮')
        self.click(controls[0], after_sleep=2)
        regions = self.ocr(.4, 0, .75, 1, match=re.compile('今州'))
        if len(regions) != 1:
            raise RuntimeError('地图列表没有唯一的今州入口')
        self.click(regions[0], after_sleep=2)
        districts = self.ocr(.7, .1, 1, .9, match='云陵谷')
        if len(districts) != 1:
            raise RuntimeError('今州地图没有唯一的云陵谷入口')
        self.click(districts[0], after_sleep=2)
        self.click(.4, .5, after_sleep=1)
        landmarks = self.ocr(.4, .35, .5, .45, match='夜明坡')
        if len(landmarks) != 1:
            raise RuntimeError('今州地图没有显示已确认的夜明坡治疗位置')
        self.click(.485, .315, after_sleep=1)
        travel = self.wait_feature('gray_teleport', time_out=5, raise_if_not_found=True)
        self.click(travel, after_sleep=2)
        self.wait_in_team_and_world(time_out=120, raise_if_not_found=True)

    def prepare_boss(self, profile_suffix, boss_name, capture_path, heal=False):
        self._allow_bring_to_front = True
        try:
            self.next_frame()
            if self.ocr(.3, .1, .7, .4, match='挑战失败'):
                exits = self.wait_ocr(.15, .65, .5, 1, match='退出副本',
                                      time_out=5, raise_if_not_found=True)
                self.click(exits[0], after_sleep=2)
                self.wait_in_team_and_world(time_out=120, raise_if_not_found=True)
            self.ensure_main(time_out=180)
        finally:
            self._allow_bring_to_front = False
        accounts = self.executor.get_task_by_class(MultiAccountDailyTask)
        profile = accounts._detect_account_from_world_profile()
        if not profile or not normalize_profile_code(profile).endswith(profile_suffix):
            raise RuntimeError('游戏特征码没有匹配指定账号')
        if heal:
            self.heal_team()
        self.load_chars()
        if {type(char).__name__ for char in self.chars if char is not None} != {'Qingxiao', 'Sanhua', 'Verina'}:
            raise RuntimeError('入场前角色识别没有匹配指定三人队伍')
        self.openF2Book('gray_book_boss')
        weekly = boss_name in ('时序命定之争', '无序边境之火', '无冠巨像之心')
        self.open_boss_book('zhange' if weekly else 'qiangdi')
        for _ in range(40):
            names = self.ocr(.36, .1, .8, .95, match=re.compile(re.escape(boss_name)))
            if names:
                if names[0].y > self.height_of_screen(.7):
                    self.scroll_relative(.8, .6, -3)
                    self.sleep(.3)
                    continue
                break
            self.scroll_relative(.8, .6, -8)
            self.sleep(.3)
        else:
            raise RuntimeError(f'敌人列表没有显示{boss_name}')
        if not cv2.imwrite(str(capture_path), self.frame):
            raise RuntimeError('保存敌人列表画面失败')
        buttons = self.find_feature('boss_proceed', box=self.box_of_screen(.84, .1, 1, .95), threshold=.8)
        if not buttons:
            raise RuntimeError('敌人列表没有前往按钮')
        button = min(buttons, key=lambda box: abs(box.y - names[0].y))
        if abs(button.y - names[0].y) > self.height_of_screen(.1):
            raise RuntimeError(f'{boss_name}与前往按钮没有匹配同一行')
        self.click(button, after_sleep=1)
        if weekly:
            levels = self.wait_ocr(.03, .113, .323, .66, match=re.compile('60'),
                                   time_out=10, raise_if_not_found=True)
            self.click(levels[0], after_sleep=1)
            self.click(.88, .911, after_sleep=2)
        feature = self.wait_feature(TRAVEL_FEATURES + ['team_start_challenge'], time_out=10,
                                    raise_if_not_found=True)
        if feature.name == 'team_start_challenge':
            self.click_team_challenge()
        else:
            self.wait_click_travel()
        self.wait_in_team_and_world(time_out=120, raise_if_not_found=True)
