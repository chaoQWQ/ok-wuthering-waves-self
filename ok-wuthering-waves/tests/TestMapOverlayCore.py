import ast
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.utils.AssetsDownloader import extract_zip_over, missing_required_assets
from src.utils.MapItemOverlay import MapItemOverlay
from src.utils.MapMarksDB import MapMarksDB
from src.utils.ChestRoute import load_chest_route, parse_chest_route, ChestRouteParseError
from src.utils.PathRoute import load_path_route
from src.utils.map_geometry import project_game_to_screen, project_screen_to_game
from src.task.MapOverlayTask import (
    CHEST_CONFIRM_DISTANCE_DEFAULT, CHEST_CONFIRM_POLL_MIGRATION_KEY,
    ChestTarget, MapOverlayTask, OverlayController, collection_type_ids,
)
from src.utils.MinimapDirectionWindow import direction_triangle_geometry
from src.utils.ChestGuidanceFilter import ChestGuidanceFilter


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestMapOverlayCore(unittest.TestCase):
    def test_chest_guidance_rejects_unconfirmed_ocr_jump(self):
        guidance = ChestGuidanceFilter()
        first = guidance.update((0, 0), "chest", 10000, 0)
        jumped = None
        for _ in range(8):
            jumped = guidance.update(
                (2500, -1800), "chest", 10000, 0,
                map_confirmed=False,
            )
        self.assertTrue(jumped.rejected)
        self.assertEqual((jumped.player_x, jumped.player_y),
                         (first.player_x, first.player_y))
        self.assertAlmostEqual(jumped.distance, first.distance)

    def test_chest_guidance_near_target_uses_nearby_state(self):
        guidance = ChestGuidanceFilter()
        sample = guidance.update((100, 100), "chest", 10700, 10000)
        self.assertTrue(sample.nearby)
        self.assertAlmostEqual(sample.distance, 700.0)

    def test_chest_guidance_locks_position_without_movement_input(self):
        guidance = ChestGuidanceFilter()
        first = guidance.update(
            (-1200, 689), "chest", -123300, 66900,
            movement_active=False, now=1.0,
        )
        drift = guidance.update(
            (-1185, 689), "chest", -123300, 66900,
            movement_active=False, now=2.0,
        )
        self.assertTrue(drift.rejected)
        self.assertEqual(
            (drift.player_x, drift.player_y),
            (first.player_x, first.player_y),
        )
        self.assertAlmostEqual(drift.bearing, first.bearing)
        self.assertAlmostEqual(drift.distance, first.distance)

    def test_qt_minimap_direction_triangle_uses_clockwise_bearing(self):
        class MinimapBox:
            x = 36
            y = 28
            width = 186
            height = 185

        north, _, _ = direction_triangle_geometry(0, MinimapBox())
        east, _, _ = direction_triangle_geometry(90, MinimapBox())
        center_x = MinimapBox.x + MinimapBox.width / 2
        center_y = MinimapBox.y + MinimapBox.height / 2
        self.assertLess(north[1], center_y)
        self.assertAlmostEqual(north[0], center_x, places=5)
        self.assertGreater(east[0], center_x)
        self.assertAlmostEqual(east[1], center_y, places=5)

    def test_chest_target_survives_temporary_unknown_map(self):
        controller = OverlayController.__new__(OverlayController)
        controller._chest_target = ChestTarget(
            "chest-1", "测试宝箱", "qzx_01", 1200.0, 2300.0, 9999.0
        )
        controller._chest_target_map_id = "8"
        controller._chest_search_enabled = lambda: True
        controller._ensure_chest_route_loaded = lambda: None

        controller._update_chest_search((10, 20), None)

        self.assertIsNotNone(controller._chest_target)
        self.assertEqual(controller._chest_target_map_id, "8")
        self.assertAlmostEqual(controller._chest_target.distance, 360.555, places=2)

    def test_chest_opening_hint_reuses_bigmap_description_and_is_cached(self):
        class Overlay:
            def __init__(self):
                self.calls = 0

            def get_location_description(self, location_id):
                self.calls += 1
                self.location_id = location_id
                return "击败附近守卫后开启"

        class Task:
            _overlay = Overlay()

        controller = OverlayController.__new__(OverlayController)
        controller.task = Task()
        controller._chest_hint_target_id = None
        controller._chest_hint_text = None
        target = ChestTarget(
            "chest-1", "测试宝箱", "qzx_01", 1200.0, 2300.0, 9999.0
        )

        first = controller._chest_hint_for_target(target)
        second = controller._chest_hint_for_target(target)

        self.assertEqual(first, "击败附近守卫后开启")
        self.assertEqual(second, first)
        self.assertEqual(Task._overlay.location_id, "chest-1")
        self.assertEqual(Task._overlay.calls, 1)

    def test_map_display_toggle_hotkey_controls_all_overlay_surfaces(self):
        class Task:
            def __init__(self):
                self.config = {
                    '_Overlay enabled': True,
                    'World map overlay': True,
                    'Map display toggle hotkey': '<ctrl>+<f8>',
                }
                self.messages = []
                self.clear_calls = 0

            def info_set(self, key, value):
                self.messages.append((key, value))

            def _clear_overlay(self):
                self.clear_calls += 1

        controller = OverlayController.__new__(OverlayController)
        controller.task = Task()
        controller._display_toggle_requested = False
        controller._display_toggle_key_down = False
        controller._display_toggle_poll_warned = False
        controller._win32_hotkey_down = lambda _codes: False
        hidden = []
        controller._hide_interaction_window = lambda: hidden.append('bigmap')
        controller._hide_minimap_direction_window = lambda: hidden.append('minimap')

        controller.on_map_display_toggle_hotkey()
        self.assertTrue(controller._process_map_display_toggle())
        self.assertFalse(controller.task.config['_Overlay enabled'])
        self.assertFalse(controller.task.config['World map overlay'])
        self.assertEqual(controller.task.clear_calls, 1)
        self.assertEqual(hidden, ['bigmap', 'minimap'])

        controller.on_map_display_toggle_hotkey()
        self.assertTrue(controller._process_map_display_toggle())
        self.assertTrue(controller.task.config['_Overlay enabled'])
        self.assertTrue(controller.task.config['World map overlay'])
        self.assertIn(
            ('High-value items display', '地图高价值物品显示：已开启'),
            controller.task.messages,
        )

    def test_map_display_hotkey_parser_supports_default_and_custom_keys(self):
        self.assertEqual(
            OverlayController._hotkey_vk_codes('<ctrl>+<f8>'),
            (0x11, 0x77),
        )
        self.assertEqual(
            OverlayController._hotkey_vk_codes('<shift>+g'),
            (0x10, ord('G')),
        )
        self.assertIsNone(
            OverlayController._hotkey_vk_codes('<ctrl>+<not-a-key>')
        )

    def test_map_display_toggle_polling_uses_rising_edge(self):
        controller = OverlayController.__new__(OverlayController)
        controller.task = type('Task', (), {
            'config': {'Map display toggle hotkey': '<ctrl>+<f8>'}
        })()
        controller._display_toggle_requested = False
        controller._display_toggle_key_down = False
        controller._display_toggle_poll_warned = False
        states = iter((False, True, True, False, True))
        controller._win32_hotkey_down = lambda _codes: next(states)

        controller._poll_map_display_toggle_hotkey()
        self.assertFalse(controller._display_toggle_requested)
        controller._poll_map_display_toggle_hotkey()
        self.assertTrue(controller._display_toggle_requested)
        controller._display_toggle_requested = False
        controller._poll_map_display_toggle_hotkey()
        self.assertFalse(controller._display_toggle_requested)
        controller._poll_map_display_toggle_hotkey()
        controller._poll_map_display_toggle_hotkey()
        self.assertTrue(controller._display_toggle_requested)

    def test_collection_confirm_polling_uses_rising_edge(self):
        class Task:
            config = {'Chest confirm hotkey': '<ctrl>+<f10>'}

            def __init__(self):
                self.messages = []

            def info_set(self, key, value):
                self.messages.append((key, value))

        controller = OverlayController.__new__(OverlayController)
        controller.task = Task()
        controller._chest_confirm_requested = False
        controller._chest_confirm_key_down = False
        controller._chest_confirm_poll_warned = False
        controller._chest_search_enabled = lambda: True
        states = iter((False, True, True, False, True))
        controller._win32_hotkey_down = lambda _codes: next(states)

        controller._poll_chest_confirm_hotkey()
        self.assertFalse(controller._chest_confirm_requested)
        controller._poll_chest_confirm_hotkey()
        self.assertTrue(controller._chest_confirm_requested)
        controller._chest_confirm_requested = False
        controller._poll_chest_confirm_hotkey()
        self.assertFalse(controller._chest_confirm_requested)
        controller._poll_chest_confirm_hotkey()
        controller._poll_chest_confirm_hotkey()
        self.assertTrue(controller._chest_confirm_requested)
        self.assertEqual(
            len([message for message in controller.task.messages
                 if message[1] == '已检测确认快捷键，正在校验目标距离']),
            2,
        )

    def test_collection_confirm_config_migrates_once(self):
        task = MapOverlayTask.__new__(MapOverlayTask)
        task.config = {
            'Chest confirm hotkey': '',
            '_Chest confirm distance (world units)': 250,
            CHEST_CONFIRM_POLL_MIGRATION_KEY: False,
        }

        task._migrate_chest_confirm_config()

        self.assertEqual(task.config['Chest confirm hotkey'], '<ctrl>+<f10>')
        self.assertEqual(
            task.config['_Chest confirm distance (world units)'],
            CHEST_CONFIRM_DISTANCE_DEFAULT,
        )
        self.assertTrue(task.config[CHEST_CONFIRM_POLL_MIGRATION_KEY])

        # After migration, deliberately clearing the key remains supported.
        task.config['Chest confirm hotkey'] = ''
        task._migrate_chest_confirm_config()
        self.assertEqual(task.config['Chest confirm hotkey'], '')

    def test_collection_confirm_accepts_previous_1273_unit_rejection(self):
        class Task:
            config = {
                '_Chest confirm distance (world units)':
                    CHEST_CONFIRM_DISTANCE_DEFAULT,
            }

            def __init__(self):
                self.messages = []

            def info_set(self, key, value):
                self.messages.append((key, value))

        controller = OverlayController.__new__(OverlayController)
        controller.task = Task()
        controller._chest_confirm_requested = True
        controller._chest_target = ChestTarget(
            'collectible-1', '测试收集物', 'qzx_01', 0, 0, 1273
        )
        controller._chest_target_map_id = '8'
        controller._completed_ids = set()
        controller._chest_search_enabled = lambda: True
        controller._chest_distance = lambda _player, _target: 1273
        controller._ensure_marks = lambda: None
        controller._persist_mark_change = lambda _old, new: set(new)
        controller._clear_chest_target = lambda: None
        controller._chest_last_query_at = 1.0

        controller._process_chest_confirm((0, 0), state_id=8)

        self.assertIn('collectible-1', controller._completed_ids)
        self.assertIn(
            ('Chest search', '已确认领取：测试收集物'),
            controller.task.messages,
        )

    def test_native_minimap_painter_uses_gdi_primitives(self):
        class NativeCanvas:
            def __init__(self):
                self.rectangles = []
                self.texts = []

            def rectangle(self, *args, **kwargs):
                self.rectangles.append((args, kwargs))

            def text(self, *args, **kwargs):
                self.texts.append((args, kwargs))

        canvas = NativeCanvas()
        callback = MapItemOverlay.make_paint_callback(
            [(100, 120, None, "宝箱", (255, 255, 0), 1.0, "id", 0)],
            status_lines=["地图：8"],
        )
        callback(
            canvas,
            object(),
        )
        # The callback must select the GDI compatibility branch rather than
        # attempting Qt-only methods on the native canvas.
        self.assertEqual(len(canvas.rectangles), 2)
        self.assertEqual(len(canvas.texts), 2)

    def test_native_minimap_painter_highlights_chest_target(self):
        class NativeCanvas:
            def __init__(self):
                self.rectangles = []
                self.texts = []

            def rectangle(self, *args, **kwargs):
                self.rectangles.append((args, kwargs))

            def text(self, *args, **kwargs):
                self.texts.append((args, kwargs))

        canvas = NativeCanvas()
        class MinimapBox:
            x = 1500
            y = 50
            width = 184
            height = 184

        callback = MapItemOverlay.make_paint_callback(
            [(100, 120, None, "宝箱", (255, 255, 0), 1.0, "id", 0)],
            edge_arrow=(90, MinimapBox(), 4520),
            target_marker=(100, 120),
        )
        callback(canvas, object())
        # Two item-marker rectangles + one red target outline + three radial
        # direction squares + two banner outlines, with a fixed label below the
        # minimap so the game's own minimap art cannot hide the cue.
        self.assertEqual(len(canvas.rectangles), 8)
        target_labels = [args for args, _ in canvas.texts if "CHEST 90 deg  4520m" in str(args)]
        self.assertEqual(len(target_labels), 1)
        self.assertGreater(target_labels[0][0], MinimapBox.x)
        self.assertGreater(target_labels[0][1], MinimapBox.y + MinimapBox.height)

    def test_overlay_and_combat_tasks_are_registered_together(self):
        config_source = (REPO_ROOT / "config.py").read_text(encoding="utf-8")
        ast.parse(config_source)
        self.assertIn('["src.task.AutoCombatTask", "AutoCombatTask"]', config_source)
        self.assertIn('["src.task.MapOverlayTask", "MapOverlayTask"]', config_source)

    def test_bundled_route_can_be_parsed(self):
        route = load_path_route(str(REPO_ROOT / "assets" / "path.json"))
        self.assertGreater(len(route.sections), 0)
        self.assertGreater(sum(len(section.nodes) for section in route.sections), 0)

    def test_video_chest_route_manifest_can_be_parsed(self):
        route = load_chest_route(str(
            REPO_ROOT / "assets" / "chest_routes" /
            "1.0_yunlinggu_yulongtai_p5.json"
        ))
        self.assertEqual(route.video_id, "BV17QwyzVEbZ")
        self.assertEqual(route.part, 5)
        self.assertEqual(route.state_id, 8)
        self.assertEqual(route.chest_count, 17)
        self.assertFalse(route.calibrated)
        self.assertEqual(route.route_label, "P5 云陵谷·玉龙台 17箱")

    def test_video_chest_route_manifest_rejects_duplicate_order(self):
        data = {
            "schema_version": 1,
            "source": "https://example.invalid/video",
            "video_id": "BV-test",
            "part": 1,
            "version": "1.0",
            "map": {"name": "测试地图", "state_id": 8},
            "counts": {"chests": 1, "sound_boxes": 0,
                       "scenic_points": 0, "butterflies": 0},
            "nodes": [{"order": 1}, {"order": 1}],
        }
        with self.assertRaises(ChestRouteParseError):
            parse_chest_route(data)

    def test_completion_marks_are_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "map_marks.db"
            db = MapMarksDB(str(db_path))
            db.add("treasure-1")
            db.add("treasure-1")
            self.assertEqual(db.load_completed(), {"treasure-1"})
            db.close()

            reopened = MapMarksDB(str(db_path))
            self.assertTrue(reopened.is_completed("treasure-1"))
            reopened.remove("treasure-1")
            self.assertFalse(reopened.is_completed("treasure-1"))
            reopened.close()

    def test_completion_marks_are_isolated_by_collection_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = MapMarksDB(str(Path(temp_dir) / "map_marks.db"))
            db.add("same-location", "uid-1001")
            db.add("other-location", "uid-2002")

            self.assertEqual(db.load_completed("uid-1001"), {"same-location"})
            self.assertEqual(db.load_completed("uid-2002"), {"other-location"})
            self.assertFalse(db.is_completed("same-location", "uid-2002"))
            db.close()

    def test_empty_collection_accounts_persist_for_dropdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "map_marks.db"
            db = MapMarksDB(str(db_path))
            self.assertEqual(db.list_accounts(), ["default"])
            db.ensure_account(" uid-2002 ")
            db.ensure_account("alias-a")
            db.close()

            reopened = MapMarksDB(str(db_path))
            self.assertEqual(
                reopened.list_accounts(),
                ["default", "alias-a", "uid-2002"],
            )
            self.assertEqual(reopened.load_completed("uid-2002"), set())
            reopened.close()

    def test_legacy_completion_marks_migrate_to_default_account(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "map_marks.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE completed_marks (location_id TEXT PRIMARY KEY)"
            )
            connection.execute(
                "INSERT INTO completed_marks(location_id) VALUES ('legacy-1')"
            )
            connection.commit()
            connection.close()

            db = MapMarksDB(str(db_path))
            self.assertEqual(db.load_completed("default"), {"legacy-1"})
            self.assertEqual(db.load_completed("uid-1001"), set())
            self.assertEqual(db.list_accounts(), ["default"])
            columns = {
                row[1] for row in
                db._conn.execute("PRAGMA table_info(completed_marks)").fetchall()
            }
            self.assertIn("account_id", columns)
            db.close()

    def test_collection_group_selection_expands_to_database_types(self):
        selected = collection_type_ids([
            "Chests", "Sonance Caskets", "Tidal Heritage", "Scenic Spots"
        ])
        self.assertIn("qzx_04", selected)
        self.assertIn("sx", selected)
        self.assertIn("sx·lgn", selected)
        self.assertIn("cx_03", selected)
        self.assertIn("gjd", selected)
        self.assertEqual(
            collection_type_ids([]), ("__no_collection_type__",)
        )

    def test_asset_extraction_rejects_parent_path_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "assets.zip"
            destination = root / "assets"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("assets/stitched/map_coords.json", "{}")
                bundle.writestr("../outside.txt", "must not be extracted")

            self.assertEqual(extract_zip_over(str(archive), str(destination)), 1)
            self.assertTrue((destination / "stitched" / "map_coords.json").is_file())
            self.assertFalse((root / "outside.txt").exists())

    def test_missing_assets_reports_required_files_and_features(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = missing_required_assets(temp_dir)
        self.assertIn("stitched/map_coords.json", missing)
        self.assertIn("stitched/map_items.db", missing)
        self.assertIn("stitched/setting.json", missing)
        self.assertIn("stitched/*_siftgz.npz", missing)

    def test_projection_round_trip(self):
        screen = project_game_to_screen(1250, -400, 1000, -500, 0.02, 960, 540)
        game = project_screen_to_game(*screen, 1000, -500, 0.02, 960, 540)
        self.assertEqual(screen, (965, 542))
        self.assertEqual(game, (1250.0, -400.0))


if __name__ == "__main__":
    unittest.main()
