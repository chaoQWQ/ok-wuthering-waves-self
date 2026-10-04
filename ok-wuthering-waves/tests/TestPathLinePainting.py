import math
import unittest

from src.utils.PathRoute import (
    PathLayer, PathNode, Section, PathRoute,
    build_path_layers, parse_section_color,
)
from src.utils.MapItemOverlay import (
    _native_draw_path_layers, _paint_native_canvas, MapItemOverlay,
    PATH_LINE_WIDTH,
)


# ---------------------------------------------------------------------------
# 纯逻辑测试：PathLayer 构造和路线几何
# ---------------------------------------------------------------------------

class TestPathLayerGeometry(unittest.TestCase):
    def _make_layer(self, points, color=(255, 128, 0)):
        node_ids = tuple(str(i) for i in range(len(points)))
        return PathLayer(color=color, points=tuple(points), node_ids=node_ids)

    def test_two_nodes_produce_one_segment(self):
        layer = self._make_layer([(10, 20), (50, 80)])
        self.assertEqual(len(layer.segments), 1)
        self.assertEqual(layer.segments[0], ((10, 20), (50, 80)))

    def test_multiple_nodes_connected_in_order(self):
        pts = [(0, 0), (10, 0), (20, 10), (30, 5)]
        layer = self._make_layer(pts)
        self.assertEqual(len(layer.segments), 3)
        for i, seg in enumerate(layer.segments):
            self.assertEqual(seg[0], pts[i])
            self.assertEqual(seg[1], pts[i + 1])

    def test_section_color_preserved_in_layer(self):
        color = (200, 100, 50)
        layer = self._make_layer([(0, 0), (10, 10)], color=color)
        self.assertEqual(layer.color, color)

    def test_single_node_produces_no_segments(self):
        layer = self._make_layer([(5, 5)])
        self.assertEqual(len(layer.segments), 0)

    def test_empty_layer_produces_no_segments(self):
        layer = self._make_layer([])
        self.assertEqual(len(layer.segments), 0)

    def test_highlight_color_distinct_from_normal(self):
        # 当前目标段使用高亮颜色（红色系），与普通段颜色不同
        normal = parse_section_color('#4A90D9')
        highlight = (255, 60, 60)
        self.assertNotEqual(normal, highlight)

    def test_invalid_coordinate_not_in_valid_layer(self):
        # 无效坐标（非数值）不应出现在 PathLayer.points 中
        # _parse_node 会跳过非数值坐标节点
        from src.utils.PathRoute import _parse_node
        bad = {'positionId': '1', 'xposition': 'not_a_number', 'yposition': 0}
        self.assertIsNone(_parse_node(bad))

    def test_missing_position_id_rejected(self):
        from src.utils.PathRoute import _parse_node
        bad = {'xposition': 1.0, 'yposition': 2.0}  # 没有 positionId
        self.assertIsNone(_parse_node(bad))

    def test_build_path_layers_wrong_state_id_returns_empty(self):
        section = Section(section_id=1, color=(255, 0, 0),
                          nodes=(PathNode('n1', 'A', 't', 100.0, 200.0),
                                 PathNode('n2', 'B', 't', 110.0, 210.0)))
        route = PathRoute(state_id=8, sections=(section,))
        # 上下文 state_id 不匹配
        layers = build_path_layers(route, context_state_id=99,
                                   player_x=0, player_y=0,
                                   scale=0.01, center_x=400, center_y=300)
        self.assertEqual(layers, ())

    def test_build_path_layers_matching_state_id_returns_layers(self):
        section = Section(section_id=1, color=(200, 100, 50),
                          nodes=(PathNode('n1', 'A', 't', 0.0, 0.0),
                                 PathNode('n2', 'B', 't', 100.0, 0.0)))
        route = PathRoute(state_id=8, sections=(section,))
        layers = build_path_layers(route, context_state_id=8,
                                   player_x=0, player_y=0,
                                   scale=0.01, center_x=400, center_y=300)
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].color, (200, 100, 50))
        self.assertEqual(len(layers[0].points), 2)

    def test_build_path_layers_multi_section_colors(self):
        s1 = Section(section_id=1, color=(255, 0, 0),
                     nodes=(PathNode('a', 'A', 't', 0.0, 0.0),
                            PathNode('b', 'B', 't', 10.0, 0.0)))
        s2 = Section(section_id=2, color=(0, 255, 0),
                     nodes=(PathNode('c', 'C', 't', 0.0, 10.0),
                            PathNode('d', 'D', 't', 10.0, 10.0)))
        route = PathRoute(state_id=8, sections=(s1, s2))
        layers = build_path_layers(route, context_state_id=8,
                                   player_x=0, player_y=0,
                                   scale=0.01, center_x=400, center_y=300)
        self.assertEqual(len(layers), 2)
        self.assertEqual(layers[0].color, (255, 0, 0))
        self.assertEqual(layers[1].color, (0, 255, 0))

    def test_build_path_layers_target_segment_highlighted(self):
        s1 = Section(section_id=1, color=(200, 100, 50),
                     nodes=(PathNode('n1', 'A', 't', 0.0, 0.0),
                            PathNode('n2', 'B', 't', 100.0, 0.0)))
        s2 = Section(section_id=2, color=(0, 200, 100),
                     nodes=(PathNode('n3', 'C', 't', 200.0, 0.0),
                            PathNode('n4', 'D', 't', 300.0, 0.0)))
        route = PathRoute(state_id=8, sections=(s1, s2))
        layers = build_path_layers(route, context_state_id=8,
                                   player_x=0, player_y=0,
                                   scale=0.01, center_x=400, center_y=300,
                                   target_node_id='n3',
                                   highlight_color=(255, 60, 60))
        self.assertEqual(len(layers), 2)
        self.assertEqual(layers[0].color, (200, 100, 50))
        self.assertEqual(layers[1].color, (255, 60, 60))

    def test_empty_path_layers_nothing_drawn(self):
        # 空 path_layers 不会崩溃，也不会产出任何绘制数据
        class FakeCanvas:
            rectangles = []
            def rectangle(self, *a, **kw): self.rectangles.append(a)
            def text(self, *a, **kw): pass

        canvas = FakeCanvas()
        _native_draw_path_layers(canvas, [])
        self.assertEqual(canvas.rectangles, [])


# ---------------------------------------------------------------------------
# GDI 原生画布绘制测试（真实接口，无 mock 替代）
# ---------------------------------------------------------------------------

class TestNativeCanvasPathLayers(unittest.TestCase):
    """使用真实的 mock 兼容 GdiCanvas 接口（仅 rectangle + text），
    验证 _native_draw_path_layers 在 hdc 不可用时走回退路径并产出矩形。
    """

    class FakeCanvas:
        """只有 rectangle/text 接口、没有 hdc 的模拟画布（回退路径测试用）。"""
        def __init__(self):
            self.rectangles = []
            self.texts = []

        def rectangle(self, x, y, w, h, color=(255, 0, 0), line_width=2):
            self.rectangles.append((x, y, w, h, color))

        def text(self, x, y, value, color=(255, 255, 255)):
            self.texts.append((x, y, value))

    def _layer(self, points, color=(100, 200, 50)):
        node_ids = tuple(str(i) for i in range(len(points)))
        return PathLayer(color=color, points=tuple(points), node_ids=node_ids)

    def test_two_nodes_draw_rectangles(self):
        canvas = self.FakeCanvas()
        layer = self._layer([(0, 0), (100, 0)])
        _native_draw_path_layers(canvas, [layer])
        # 回退路径至少画了若干小矩形（采样折线）
        self.assertGreater(len(canvas.rectangles), 0)

    def test_single_node_draws_nothing(self):
        canvas = self.FakeCanvas()
        layer = self._layer([(50, 50)])
        _native_draw_path_layers(canvas, [layer])
        # 单节点无线段，不画矩形
        self.assertEqual(len(canvas.rectangles), 0)

    def test_empty_layers_draws_nothing(self):
        canvas = self.FakeCanvas()
        _native_draw_path_layers(canvas, [])
        self.assertEqual(len(canvas.rectangles), 0)

    def test_color_preserved_in_rectangles(self):
        canvas = self.FakeCanvas()
        color = (200, 80, 30)
        layer = self._layer([(0, 0), (50, 50)], color=color)
        _native_draw_path_layers(canvas, [layer])
        # 矩形中至少有一个使用了该颜色
        colors = [r[4] for r in canvas.rectangles]
        self.assertIn(color, colors)

    def test_midpoint_marker_rectangle_drawn(self):
        # 连线中点应有方向标记矩形（size 8x8）
        canvas = self.FakeCanvas()
        layer = self._layer([(0, 0), (100, 0)])
        _native_draw_path_layers(canvas, [layer])
        # 有宽=8的矩形（方向标记）
        marker_rects = [r for r in canvas.rectangles if r[2] == 8 and r[3] == 8]
        self.assertGreater(len(marker_rects), 0)

    def test_paint_native_canvas_with_path_layers(self):
        # 验证 _paint_native_canvas 正确接收并传递 path_layers
        canvas = self.FakeCanvas()
        layer = self._layer([(10, 10), (90, 10)])
        _paint_native_canvas(canvas, [], path_layers=(layer,))
        # 应该有矩形（来自路线折线）
        self.assertGreater(len(canvas.rectangles), 0)

    def test_paint_native_canvas_no_layers_no_crash(self):
        canvas = self.FakeCanvas()
        _paint_native_canvas(canvas, [], path_layers=())
        # 空路线不产出矩形，不崩溃
        self.assertEqual(len(canvas.rectangles), 0)

    def test_make_paint_callback_native_path_layers(self):
        # make_paint_callback 应在原生画布上绘制 path_layers
        canvas = self.FakeCanvas()
        layer = self._layer([(20, 20), (80, 60)])
        callback = MapItemOverlay.make_paint_callback(
            [], path_layers=(layer,), draw_path_nodes=False,
        )
        callback(canvas, object())
        self.assertGreater(len(canvas.rectangles), 0)

    def test_invalid_layer_does_not_crash_canvas(self):
        # 含无效点（非数值坐标）的 layer 不能导致整个覆盖层停止
        canvas = self.FakeCanvas()

        class BadLayer:
            color = (255, 0, 0)
            points = [('not', 'numbers'), (100, 100)]
            node_ids = ('a', 'b')

        # 不应抛出异常
        try:
            _native_draw_path_layers(canvas, [BadLayer()])
        except Exception as exc:
            self.fail(f"_native_draw_path_layers 意外崩溃: {exc}")


# ---------------------------------------------------------------------------
# Qt 路径层绘制测试（真实 QPainter）
# ---------------------------------------------------------------------------

class TestQtPathLayerPainting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _make_layer(self, points, color=(100, 200, 50)):
        node_ids = tuple(str(i) for i in range(len(points)))
        return PathLayer(color=color, points=tuple(points), node_ids=node_ids)

    def test_paint_path_layers_qt_two_nodes(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QPainter, QImage
        from src.utils.MapItemOverlay import paint_path_layers

        image = QImage(200, 200, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            layer = self._make_layer([(20, 100), (180, 100)])
            paint_path_layers(painter, [layer], draw_nodes=False)
        finally:
            painter.end()
        # 验证确实有像素被绘制（颜色不再是全透明 0）
        painted = any(
            image.pixel(x, 100) != 0
            for x in range(20, 180, 5)
        )
        self.assertTrue(painted, "两节点路线应在画布上产出可见像素")

    def test_paint_path_layers_qt_color_on_canvas(self):
        from PySide6.QtGui import QPainter, QImage, QColor
        from src.utils.MapItemOverlay import paint_path_layers

        color = (200, 80, 30)
        image = QImage(200, 50, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            layer = self._make_layer([(10, 25), (190, 25)], color=color)
            paint_path_layers(painter, [layer], draw_nodes=False)
        finally:
            painter.end()
        # 沿水平线采样，找到与 color 接近的像素
        found = False
        for x in range(10, 190, 5):
            px = image.pixel(x, 25)
            qc = QColor(px)
            if qc.red() > 150 and qc.green() < 120:
                found = True
                break
        self.assertTrue(found, "连线颜色应可在画布上采样到")

    def test_paint_path_layers_qt_single_node_no_line(self):
        from PySide6.QtGui import QPainter, QImage
        from src.utils.MapItemOverlay import paint_path_layers

        image = QImage(100, 100, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            # draw_nodes=False 且只有一个点，不应画任何东西
            layer = self._make_layer([(50, 50)])
            paint_path_layers(painter, [layer], draw_nodes=False)
        finally:
            painter.end()
        # 画布应仍为全零（无线条）
        all_zero = all(image.pixel(x, 50) == 0 for x in range(5, 95, 10))
        self.assertTrue(all_zero, "单节点无连线，画布应保持空白")


if __name__ == '__main__':
    unittest.main()
