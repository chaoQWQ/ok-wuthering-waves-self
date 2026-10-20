import math
from unittest import TestCase

from src.utils.map_geometry import (
    SCENE_WORLD_SCALE,
    get_minimap_geometry,
    project_minimap_coordinate,
    project_bigmap_coordinate,
)


class TestImaoMapGeometry(TestCase):
    def test_minimap_geometry_1080p(self):
        geom = get_minimap_geometry(1920, 1080)
        self.assertAlmostEqual(geom["hud_scale"], 1.2, places=4)
        self.assertAlmostEqual(geom["width"], 184.8, places=4)
        self.assertAlmostEqual(geom["height"], 184.8, places=4)
        self.assertAlmostEqual(geom["center_x"], 128.4, places=4)
        self.assertAlmostEqual(geom["center_y"], 120.0, places=4)
        self.assertAlmostEqual(geom["radius"], 92.4, places=4)
        self.assertAlmostEqual(geom["pixels_per_map_unit"], 0.952577, places=4)

    def test_project_minimap_coordinate_inside_and_outside(self):
        sx, sy, dist, inside = project_minimap_coordinate(
            item_x=1000.0,
            item_y=2000.0,
            player_x=1000.0,
            player_y=2000.0,
            client_width=1920,
            client_height=1080,
        )
        self.assertEqual(dist, 0.0)
        self.assertTrue(inside)
        self.assertEqual(sx, 128)
        self.assertEqual(sy, 120)

        sx_far, sy_far, dist_far, inside_far = project_minimap_coordinate(
            item_x=20000.0,
            item_y=2000.0,
            player_x=1000.0,
            player_y=2000.0,
            client_width=1920,
            client_height=1080,
        )
        self.assertFalse(inside_far)
        self.assertGreater(dist_far, 100.0)
        self.assertGreater(sx_far, 128 + 92)

    def test_project_bigmap_coordinate(self):
        sx, sy = project_bigmap_coordinate(
            item_x=5000.0,
            item_y=5000.0,
            center_x=5000.0,
            center_y=5000.0,
            client_width=1920,
            client_height=1080,
            scaling_factor=1.0,
        )
        self.assertEqual(sx, 960)
        self.assertEqual(sy, 540)
