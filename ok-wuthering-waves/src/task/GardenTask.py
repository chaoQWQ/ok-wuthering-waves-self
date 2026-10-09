import re
import time


from ok import Logger, run_task
from config import config
from src.Labels import Labels
from src.task.BaseWWTask import BaseWWTask
from src.task.WWOneTimeTask import WWOneTimeTask

logger = Logger.get_logger(__name__)


class GardenTask(WWOneTimeTask, BaseWWTask):
    GARDEN_TARGET_POINTS = re.compile('6000')
    GARDEN_SPEED_PATTERN = re.compile(r'MAX|\d+[.,]\d+', re.IGNORECASE)
    # 乐园界面元素连续无匹配的容忍时长；正常游玩时每隔几秒就能匹配到元素
    GARDEN_NO_MATCH_TIMEOUT = 120
    GARDEN_NO_MATCH_LOG_INTERVAL = 15

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🎡 自动周常乐园"
        self.description = "Detect and click garden actions until the task is stopped."
        self.garden_features = [
            label.value for label in Labels
            if label.value.startswith("garden_")
        ]
        self.garden_priority_features = [
            "garden_get_skip",
            "garden_not_interested_confirm",
        ]

    def run(self):
        WWOneTimeTask.run(self)
        self.ensure_main()
        self.open_garden_weekly_page()
        if self.is_weekly_garden_completed():
            self.log_info('乐园任务完成, 已达到上限', notify=True)
            return
        self.click(0.246, 0.486, after_sleep=1)
        no_match_since = time.monotonic()
        last_no_match_log = no_match_since
        while True:
            self.sleep(0.1)
            target = self.find_best_garden_feature()
            self.sleep(0.2)
            if target:
                no_match_since = time.monotonic()
                self.info_set("current task", target.name)
                if target.name == 'garden_get_skip':
                    self.sleep(1)
                    self.log_info(f"click garden_get_confirm")
                    if gold := self.find_one('garden_get_gold', horizontal_variance=0.9):
                        self.click(gold, after_sleep=1)
                    elif purple := self.find_one('garden_get_purple', horizontal_variance=0.9):
                        self.click(purple, after_sleep=1)
                    else:
                        self.click(0.5, 0.2, after_sleep=1)
                    self.click(self.get_box_by_name('garden_get_confirm_gray'), after_sleep=1)
                    continue
                elif target.name == 'garden_not_interested':
                    not_interested = self.find_feature('garden_not_interested', vertical_variance=0.4)
                    self.click(not_interested[-1], after_sleep=1)
                    self.click(self.get_box_by_name('garden_not_interested_confirm'), after_sleep=1)
                    continue
                elif target.name == 'garden_start_game':
                    # At Garden Entrance, choose blessing1
                    self._choose_first_blessing()
                elif target.name == 'garden_next_day':
                    self.ensure_garden_max_speed()
                    target = self.find_best_garden_feature()
                    if target is None or target.name != 'garden_next_day':
                        continue
                self.log_info(f"click {target.name} {target.confidence:.3f}")
                self.click(target, after_sleep=1)
            else:
                garden_restart = self.find_one('a_garden_restart')
                garden_back = self.find_one('a_garden_back')
                if garden_restart and garden_back:
                    no_match_since = time.monotonic()
                    # 避免因点击太快，导致[挑战失败]页面中点击[返回主页]失败
                    self.sleep(2)
                    texts = self.ocr(0.373, 0.346, 0.859, 0.615)
                    self.log_info('garden end {}'.format(texts))
                    if self.is_garden_done(texts):
                        self.click(garden_back, after_sleep=1)
                        if self.wait_feature('garden_start_game', settle_time=1, time_out=5):
                            self.back(after_sleep=1)
                        if self.wait_book('gray_book_quest', time_out=30):
                            self.click(0.927, 0.893, after_sleep=2)
                            self.click(0.927, 0.893, after_sleep=1)
                        break
                    else:
                        self.click(garden_restart, after_sleep=1)
                else:
                    self.ensure_garden_max_speed()
                    idle_seconds = time.monotonic() - no_match_since
                    if idle_seconds > self.GARDEN_NO_MATCH_TIMEOUT:
                        self.screenshot('garden_page_not_detected')
                        raise Exception(
                            f'已 {idle_seconds:.0f} 秒未识别到任何乐园界面元素，'
                            '乐园页面可能没有打开（检查索拉指南活动页入口是否变化），终止乐园任务以避免卡死')
                    if time.monotonic() - last_no_match_log >= self.GARDEN_NO_MATCH_LOG_INTERVAL:
                        last_no_match_log = time.monotonic()
                        self.log_warning(f'已 {idle_seconds:.0f} 秒未识别到乐园界面元素，继续等待')
                self.sleep(0.2)
        self.log_info('乐园任务完成, 已达到上限', notify=True)

    def find_garden_speed(self):
        if maximum := self.find_one('the_garden_max'):
            return maximum
        speeds = self.ocr(0.63, 0.03, 0.707, 0.09, match=self.GARDEN_SPEED_PATTERN)
        return next(iter(speeds), None)

    def ensure_garden_max_speed(self):
        if self.has_garden_popup():
            return
        speed = self.find_garden_speed()
        if speed is None or speed.name == 'the_garden_max' or 'MAX' in speed.name.upper():
            return

        self.log_info('将游园速度设置为 MAX')
        self.click(self.get_box_by_name('the_garden_max'), after_sleep=0.3)
        if not self.wait_until(self._advance_garden_speed, time_out=10, raise_if_not_found=False):
            self.log_warning('游园速度切换 MAX 超时，继续执行任务')

    def _advance_garden_speed(self):
        if self.has_garden_popup():
            return True
        speed = self.find_garden_speed()
        if speed is None:
            return False
        if speed.name == 'the_garden_max' or 'MAX' in speed.name.upper():
            return True
        self.click(self.get_box_by_name('the_garden_max'), after_sleep=0.3)
        return False

    def has_garden_popup(self):
        popup_features = [name for name in self.garden_features if name != 'garden_next_day']
        popup_features.extend(('a_garden_restart', 'a_garden_back'))
        return any(
            self.feature_exists(name) and self.find_one(
                name, vertical_variance=0.4 if name == 'garden_not_interested' else 0,
            )
            for name in popup_features
        )

    def open_garden_weekly_page(self):
        self.openF2Book('gray_book_quest')
        self.sleep(1)
        self.click(0.343, 0.129, after_sleep=1)
        self.click(0.927, 0.893, after_sleep=3)
        self.click(0.927, 0.893, after_sleep=2)

    def is_weekly_garden_completed(self):
        current = self.ocr(0.102, 0.793, 0.284, 0.956, match=self.GARDEN_TARGET_POINTS)
        self.log_info(f"Garden current: {current}")
        return bool(current)

    def is_garden_done(self, texts):
        text = " ".join(str(getattr(box, "name", box)) for box in texts)
        return text.count(self.GARDEN_TARGET_POINTS.pattern) != 1

    def find_best_garden_feature(self):
        matches = []
        for feature_name in self.garden_features:
            if not self.feature_exists(feature_name):
                continue
            if feature_name == 'garden_get_confirm_gray' or feature_name == 'garden_not_interested_confirm':
                continue
            if feature_name == 'garden_not_interested':
                matches.extend(self.find_feature(feature_name, vertical_variance=0.4))
            else:
                matches.extend(self.find_feature(feature_name))
        for priority_feature in self.garden_priority_features:
            priority_matches = [
                match for match in matches
                if match.name == priority_feature
            ]
            if priority_matches:
                return max(priority_matches, key=lambda box: box.confidence)
        return max(matches, key=lambda box: box.confidence, default=None)

    def _choose_first_blessing(self):
        """At Garden Entrance, choose first blessing"""
        # click blessing botton
        self.click(965 / 1920, 860 / 1080, after_sleep=2)
        # choose blessing1(Add-on)
        self.click(700 / 1920, 666 / 1080, after_sleep=2)
        # confirm
        self.click(1600 / 1920, 900 / 1080, after_sleep=2)


if __name__ == "__main__":
    run_task(config, task=GardenTask, debug=True)
