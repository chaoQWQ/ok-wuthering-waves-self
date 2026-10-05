from pathlib import Path
import re
import unittest

from src.utils.QuestTargetSearch import QuestTargetSearch


class TestQuestTargetSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = (Path(__file__).parent / "data" / "quest_near_navigation_log.txt").read_text(encoding="utf-8-sig")
        cls.distances = [float(value) for value in re.findall(r"任务目标还有 (\d+(?:\.\d+)?) 米", text)]
        if not cls.distances:
            raise ValueError("近距离移动的实际运行记录不存在")

    def test_recorded_stationary_distance_limits_forward_probes(self):
        search = QuestTargetSearch()
        distance = self.distances[0]
        search.remember_forward_target(distance)
        samples = [sample for sample in self.distances if sample == distance]
        self.assertGreater(len(samples), 3)
        actions = [search.next_forward_probe(sample) for sample in samples]
        self.assertEqual(actions[:3], [.15, .15, .15])
        self.assertTrue(all(action == 0 for action in actions[3:]))

    def test_recorded_distance_reduction_allows_forward_probe(self):
        search = QuestTargetSearch()
        search.remember_forward_target(self.distances[0])
        closer = next(distance for distance in self.distances if distance < self.distances[0])
        self.assertEqual(search.next_forward_probe(closer), .15)

    def test_camera_search_clears_previous_forward_direction(self):
        search = QuestTargetSearch()
        search.remember_forward_target(self.distances[0])
        search.next_turn()
        self.assertEqual(search.next_forward_probe(self.distances[0]), 0)

    def test_objective_transition_clears_previous_forward_direction(self):
        search = QuestTargetSearch()
        search.remember_forward_target(self.distances[0])
        search.reset()
        self.assertEqual(search.next_forward_probe(self.distances[0]), 0)


if __name__ == "__main__":
    unittest.main()
