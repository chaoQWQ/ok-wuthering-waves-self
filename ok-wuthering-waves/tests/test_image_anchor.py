import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import cv2
import numpy as np
from ok import Box
from PySide6.QtWidgets import QApplication

from src.utils.ImageAnchoredProjection import ImageAnchoredProjection, LogicalProjection
from src.utils.MinimapDirectionWindow import MinimapDirectionWindow
from src.utils.ScreenMotionTracker import ScreenMotionTracker
from src.utils.OverlayMotion import transform_overlay
from src.utils.MapItemOverlay import MapItemOverlay
from src.utils.InteractionOverlayWindow import BubbleSpec, InteractionOverlayWindow
from src.utils.overlay_types import Rect


ROOT = Path(__file__).resolve().parents[1]


def read_map():
    image = cv2.imread(str(ROOT / 'tests/images/big_map.png'))
    assert image is not None
    h, w = image.shape[:2]
    return image[round(h * .2):round(h * .8), round(w * .2):round(w * .8)].copy()


def test_real_map_tracks_translation_and_zoom():
    image = read_map()
    h, w = image.shape[:2]
    tracker = ScreenMotionTracker()
    assert tracker.anchor(image, 0)
    for scale, dx, dy in ((1, 12, -8), (1.025, -7, 10), (.98, 5, -4)):
        # 已知几何变换作用于真实地图截图，检查投影的数值误差。
        moved = cv2.warpAffine(image, np.float64([[scale, 0, dx], [0, scale, dy]]), (w, h))
        transform = tracker.track(moved, .1)
        assert transform is not None
        for point in ((w * .3, h * .3), (w * .7, h * .7)):
            expected = point[0] * scale + dx, point[1] * scale + dy
            assert math.dist(transform.project(*point), expected) < 1.0


def test_stationary_reference_does_not_accumulate_motion():
    image = read_map()
    h, w = image.shape[:2]
    projection = ImageAnchoredProjection()
    first = projection.publish(image, 0, (0, 0, w, h), (1000, 2000), 50, 'map')
    reference = projection.tracker.reference_gray
    for index in range(1, 100):
        timestamp = index / 20
        projection.observe(image, timestamp)
        observed = projection.publish(image, timestamp, (0, 0, w, h),
                                      (1000 + index % 3 * 10, 2000), 50, 'map')
        assert observed.center == first.center
        assert projection.tracker.reference_gray is reference
    assert projection.state(now=4.96).center == first.center
    assert projection.state(now=7.0) is None


def test_image_motion_preserves_world_target_and_logical_projection():
    image = read_map()
    h, w = image.shape[:2]
    projection = ImageAnchoredProjection()
    projection.publish(image, 0, (0, 0, w, h), (0, 0), 50, 'map')
    moved = cv2.warpAffine(image, np.float64([[1, 0, 15], [0, 1, -9]]), (w, h))
    state = projection.observe(moved, .1)
    assert state is not None
    assert math.dist(state.center, (-750, 450)) < 30
    logical = LogicalProjection(projection, .8)
    assert projection.state(now=.11) is not None
    assert logical.state(now=.11) is not None
    assert math.isclose(logical.state(now=.11).game_scale, state.game_scale / .8)
    assert math.isclose(state.game_scale / .8, 62.5, abs_tol=.2)
    items, _, _, _ = transform_overlay(
        [(80, 80)], (), None, (), (0, 0), 62.5, state.center, state.game_scale / .8,
        (400, 300), pixel_center=(80, 80))
    assert math.dist(items[0][:2], (92, 73)) < 1.5


def test_delayed_readout_keeps_latest_captured_terrain():
    image = read_map()
    h, w = image.shape[:2]
    projection = ImageAnchoredProjection()
    projection.publish(image, 0, (0, 0, w, h), (0, 0), 50, 'map')
    moved = cv2.warpAffine(image, np.float64([[1, 0, 15], [0, 1, -9]]), (w, h))
    observed = projection.observe(moved, .5)
    published = projection.publish(image, .1, (0, 0, w, h), (0, 0), 50, 'map')
    assert math.dist(published.center, observed.center) < 0.01
    assert projection.state(now=.6).t == .5
    projection.observe(moved, 4)
    assert projection.state(now=4) is not None
    assert projection.observe(moved, 7) is None
    assert projection.state(now=7) is None


def test_map_transition_invalidates_previous_render_context():
    image = read_map()
    h, w = image.shape[:2]
    projection = ImageAnchoredProjection()
    projection.publish(image, time.monotonic(), (0, 0, w, h), (0, 0), 50, 'map')
    logical = LogicalProjection(projection, .8)
    assert logical.state() is not None
    projection.publish(image, time.monotonic(), (0, 0, w, h), (0, 0), 50, 'other-map')
    assert logical.state() is None


def test_real_qt_latest_frame_and_dpi_minimap_center():
    app = QApplication.instance() or QApplication([])
    window = MinimapDirectionWindow()
    box = Box(36, 28, 186, 186)
    try:
        for index in range(100):
            window.render_direction((0, 0, 1536, 864), 90, box, index,
                                    target_marker=(129, 121), source_size=(1920, 1080))
        app.processEvents()
        assert window._distance == 99
        image = window.grab().toImage()
        ratio = image.width() / 1920
        cx, cy = round(129 * ratio), round(121 * ratio)
        assert image.pixelColor(cx, cy).alpha() > 0
        assert image.pixelColor(round(cx + 23 * ratio), cy).alpha() == 0
        window.hide_overlay()
        window.render_direction((0, 0, 1536, 864), 90, box, 20,
                                target_marker=(129, 121), source_size=(1920, 1080))
        app.processEvents()
        assert window.isVisible()
        assert window._distance == 20
    finally:
        window.close()
        app.processEvents()


def test_real_map_database_can_close_after_task_thread_exits():
    app = QApplication.instance() or QApplication([])
    with ThreadPoolExecutor(max_workers=1) as executor:
        overlay = executor.submit(MapItemOverlay, str(ROOT / 'assets' / 'stitched' / 'map_items.db')).result()
        try:
            rows = executor.submit(overlay.query_nearby, 0, 0, 10000000).result()
            assert rows
        finally:
            overlay.close()
    app.processEvents()


def test_bigmap_frame_uses_current_image_and_preserves_stationary_description():
    app = QApplication.instance() or QApplication([])
    image = read_map()
    h, w = image.shape[:2]
    projection = ImageAnchoredProjection()
    now = time.monotonic()
    projection.publish(image, now, (0, 0, w, h), (0, 0), 50, 'map')
    window = InteractionOverlayWindow()
    try:
        window.setGeometry(0, 0, 400, 300)
        window.show()
        window._do_frame([], BubbleSpec(200, 150, '收集说明'), (), (),
                         viewport=(LogicalProjection(projection, 1), (0, 0), 50, None, ()))
        assert window.isVisible()
        assert window._bubble.text == '收集说明'
        moved = cv2.warpAffine(image, np.float64([[1, 0, 15], [0, 1, -9]]), (w, h))
        projection.observe(moved, time.monotonic())
        window._do_frame([], None, (), (Rect(190, 140, 210, 160),), target_marker=(200, 150),
                         viewport=(LogicalProjection(projection, 1), (0, 0), 50, None, ()))
        assert math.dist(window._target_marker, (215, 141)) < 1.5
        assert math.dist((window._hitboxes_with_z[0][0].left, window._hitboxes_with_z[0][0].top),
                         (205, 131)) < 1.5
    finally:
        window.close()
        app.processEvents()
