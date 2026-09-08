import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.utils.RouteCandidateExpansion import (
    expand_review_candidates,
    reliable_probe_positions,
)


class TestRouteCandidateExpansion(unittest.TestCase):
    def test_reliable_positions_reject_low_inlier_and_implausible_scale(self):
        payload = {"results": [
            {"order": 1, "probe": {"player_game": [10, 20],
             "inlier_count": 3, "match_count": 5, "map_scale": 0.77}},
            {"order": 2, "probe": {"player_game": [30, 40],
             "inlier_count": 2, "match_count": 5, "map_scale": 0.77}},
            {"order": 3, "probe": {"player_game": [50, 60],
             "inlier_count": 4, "match_count": 5, "map_scale": 4}},
        ]}
        self.assertEqual({1: [(10.0, 20.0)]},
                         reliable_probe_positions([payload]))

    def test_expansion_respects_exact_type_and_radius(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "map.db"
            with closing(sqlite3.connect(db)) as connection:
                connection.executescript("""
                    CREATE TABLE item (id TEXT PRIMARY KEY, name TEXT);
                    CREATE TABLE location (
                        id TEXT PRIMARY KEY, item_id TEXT, state_id INTEGER,
                        type_id TEXT, x REAL, y REAL, description TEXT);
                    INSERT INTO item VALUES ('a', '朴素'), ('b', '基准');
                    INSERT INTO location VALUES
                        ('near', 'a', 8, 'qzx_01', 100, 100, 'near'),
                        ('wrong', 'b', 8, 'qzx_02', 100, 100, 'wrong'),
                        ('far', 'a', 8, 'qzx_01', 10000, 10000, 'far');
                """)
                connection.commit()
            review = [{"order": 1, "category": "chest",
                       "candidates": [], "candidate_count": 0}]
            probe = {"results": [{"order": 1, "probe": {
                "player_game": [0, 0], "inlier_count": 3,
                "match_count": 4, "map_scale": 0.8}}]}
            expanded = expand_review_candidates(
                review, db, 8, ["qzx_01"], [probe], radius=1000)
        self.assertEqual(["near"], [c["location_id"]
                                    for c in expanded[0]["candidates"]])


if __name__ == "__main__":
    unittest.main()
