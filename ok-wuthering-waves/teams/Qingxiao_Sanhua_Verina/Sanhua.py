from src.Labels import Labels
from src.char.BaseChar import BaseChar


class Sanhua(BaseChar):
    def team_rotation(self):
        qingxiao = next(char for char in self.task.chars if char.char_name == Labels.char_qingxiao)
        return qingxiao.team_rotation()

    def do_perform(self):
        self.team_rotation().perform(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        return self.team_rotation().priority(self)

    def get_buff_time(self):
        return 14
