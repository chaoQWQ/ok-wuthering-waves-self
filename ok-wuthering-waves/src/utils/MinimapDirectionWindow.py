from __future__ import annotations

import ctypes
import math
import threading

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget
from src.utils.MapDistance import format_distance_meters


def direction_triangle_geometry(bearing_deg, minimap_box):
    """Return ``(tip, left, right)`` for a clockwise-from-north bearing."""
    bx = float(minimap_box.x)
    by = float(minimap_box.y)
    bw = float(minimap_box.width)
    bh = float(minimap_box.height)
    cx = bx + bw / 2.0
    cy = by + bh / 2.0
    angle = math.radians(float(bearing_deg) % 360.0)
    dx = math.sin(angle)
    dy = -math.cos(angle)
    px = -dy
    py = dx
    radius = max(20.0, min(bw, bh) / 2.0 - 8.0)
    tip = (cx + dx * radius, cy + dy * radius)
    base_x = cx + dx * (radius - 30.0)
    base_y = cy + dy * (radius - 30.0)
    left = (base_x + px * 15.0, base_y + py * 15.0)
    right = (base_x - px * 15.0, base_y - py * 15.0)
    return tip, left, right


class MinimapDirectionWindow(QWidget):
    """Transparent, non-activating and fully click-through direction window."""

    _frame_requested = Signal(int)
    _hide_requested = Signal(int)
    _close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._bearing = None
        self._minimap_box = None
        self._distance = None
        self._target_marker = None
        self._target_pixmap = None
        self._nearby = False
        self._hint_text = ""
        self._position_note = ""
        self._guide_target = None
        self._guide_pixmap = None
        self._guide_description = None
        self._guide_status = ""
        self._guide_cache = None
        self._no_activate_applied = False
        self._last_frame = None
        self._frame_lock = threading.Lock()
        self._pending_frame = None
        self._frame_epoch = 0
        self._draw_items = ()
        self._status_lines = ()
        self._source_size = None
        self._projection = None
        self._motion_frame = None
        self._motion_timer = QTimer(self)
        self._motion_timer.setInterval(16)
        self._motion_timer.timeout.connect(self._refresh_motion)
        self._frame_requested.connect(self._consume_frame, Qt.QueuedConnection)
        self._hide_requested.connect(self._consume_hide, Qt.QueuedConnection)
        self._close_requested.connect(self._do_close, Qt.QueuedConnection)

    def render_direction(self, geometry, bearing, minimap_box, distance=None,
                         target_marker=None, nearby=False, hint_text="", guide_target=None,
                         position_note="", target_pixmap=None, draw_items=(), status_lines=(),
                         source_size=None, projection=None):
        frame = (
            tuple(geometry), float(bearing), minimap_box,
            None if distance is None else float(distance),
            None if target_marker is None else tuple(target_marker), bool(nearby),
            str(hint_text or ""),
            guide_target,
            str(position_note),
            target_pixmap,
            tuple(draw_items), tuple(status_lines or ()), source_size, projection,
        )
        with self._frame_lock:
            notify = self._pending_frame is None
            self._pending_frame = frame
            epoch = self._frame_epoch
        if notify:
            self._frame_requested.emit(epoch)

    def _consume_frame(self, epoch):
        with self._frame_lock:
            if epoch != self._frame_epoch:
                return
            frame, self._pending_frame = self._pending_frame, None
        if frame is not None:
            self._do_frame(*frame)

    def close_overlay(self):
        self._close_requested.emit()

    def _do_close(self):
        self._do_hide()
        self._guide_target = None
        if self._guide_cache is not None:
            self._guide_cache.stop()
        self.close()

    def hide_overlay(self):
        with self._frame_lock:
            self._frame_epoch += 1
            self._pending_frame = None
            epoch = self._frame_epoch
        self._hide_requested.emit(epoch)

    def _consume_hide(self, epoch):
        with self._frame_lock:
            if (epoch != self._frame_epoch or self._pending_frame is not None
                    or getattr(self, '_pending_routes', None) is not None):
                return
        self._do_hide()

    def _do_frame(self, geometry, bearing, minimap_box, distance, target_marker,
                  nearby, hint_text, guide_target=None, position_note="", target_pixmap=None,
                  draw_items=(), status_lines=(), source_size=None, projection=None):
        geometry = tuple(int(value) for value in geometry)
        frame_key = (
            geometry, float(bearing), minimap_box.x, minimap_box.y,
            minimap_box.width, minimap_box.height, distance, target_marker,
            bool(nearby), str(hint_text or ""),
            guide_target,
            position_note,
            target_pixmap,
            draw_items, status_lines, source_size,
        )
        if self.isVisible() and frame_key == self._last_frame and projection is None:
            return
        self._last_frame = frame_key
        if self.geometry().getRect() != geometry:
            self.setGeometry(*geometry)
        self._bearing = float(bearing)
        self._minimap_box = minimap_box
        self._distance = distance
        self._target_marker = target_marker
        self._target_pixmap = target_pixmap
        self._draw_items = draw_items
        self._status_lines = status_lines
        self._source_size = source_size
        self._projection = projection
        self._motion_frame = (draw_items, target_marker)
        self._nearby = bool(nearby)
        self._hint_text = str(hint_text or "")
        self._position_note = position_note
        if guide_target != self._guide_target:
            self._guide_target = guide_target
            self._guide_pixmap = None
            self._guide_description = None
            self._guide_status = ""
            if guide_target is not None:
                self._load_guide(guide_target)
        if not self.isVisible():
            self.show()
            self.raise_()
        if projection is not None:
            self._motion_timer.start()
            self._refresh_motion()
        else:
            self._motion_timer.stop()
        self.update()

    def _refresh_motion(self):
        if self._projection is None:
            return
        provider, origin, game_scale, target = self._projection
        try:
            state = provider.state()
        except RuntimeError:
            self._do_hide()
            raise
        if state is None:
            self._do_hide()
            return
        box = self._minimap_box
        cx, cy = box.x + box.width / 2, box.y + box.height / 2
        from src.utils.OverlayMotion import transform_overlay
        items, _, marker, _ = transform_overlay(
            self._motion_frame[0], (), self._motion_frame[1], (), origin, game_scale,
            state.center, state.game_scale, self._source_size, pixel_center=(cx, cy))
        self._draw_items = items
        if target is not None:
            dx, dy = target[0] - state.center[0], target[1] - state.center[1]
            self._distance = math.hypot(dx, dy)
            self._bearing = math.degrees(math.atan2(dx, -dy)) % 360
            self._nearby = self._distance <= 800
            marker = (cx + dx / state.game_scale, cy + dy / state.game_scale)
        radius = min(box.width, box.height) / 2
        self._target_marker = (marker if marker is not None and
                               math.hypot(marker[0] - cx, marker[1] - cy) <= radius else None)
        self.update()

    def _load_guide(self, target):
        from src.utils.GuideImageCache import GuideImageCache
        if self._guide_cache is None:
            self._guide_cache = GuideImageCache()
        self._guide_status = "loading"

        def details_ready(details, status):
            if self._guide_target != target:
                return
            self._guide_status = status
            if status == "ready":
                content = details['content']
                self._guide_description = str(content.get('description') or '')
                urls = content.get('picturesUrl') or []
                if urls:
                    self._guide_status = "loading"

                    def image_ready(_image, image_status):
                        if self._guide_target == target:
                            self._guide_status = image_status
                            self._guide_pixmap = self._guide_cache.get_pixmap(target[1], urls[0]) if image_status == "ready" else None
                            self.update()

                    self._guide_cache.request_image(target[1], urls[0], image_ready)
                else:
                    self._guide_status = "no_image"
            self.update()

        self._guide_cache.request_details(target[0], target[1], details_ready)

    def _do_hide(self):
        self._motion_timer.stop()
        self._projection = None
        self._last_frame = None
        self._bearing = None
        self._target_marker = None
        self._target_pixmap = None
        self._hint_text = ""
        if self.isVisible():
            self.hide()

    def _apply_no_activate(self):
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, -20)
            # WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            user32.SetWindowLongW(
                hwnd, -20, ex_style | 0x08000000 | 0x00000020 | 0x00000080
            )
            self._no_activate_applied = True
        except Exception:
            # Qt flags/attributes already provide the same behaviour; native
            # style application is only an additional Windows safeguard.
            pass

    def showEvent(self, event):
        super().showEvent(event)
        if not self._no_activate_applied:
            self._apply_no_activate()

    def paintEvent(self, event):
        if self._bearing is None or self._minimap_box is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            if self._source_size is not None:
                painter.scale(self.width() / self._source_size[0], self.height() / self._source_size[1])
            from src.utils.MapItemOverlay import paint_icon_background, paint_status_panel
            box = self._minimap_box
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(QRectF(box.x, box.y, box.width, box.height))
            painter.setClipPath(clip)
            for item in self._draw_items:
                x, y, pixmap, name, color = item[:5]
                painter.setOpacity(item[5] if len(item) > 5 else 1.0)
                if pixmap is not None and not pixmap.isNull():
                    paint_icon_background(painter, x, y, 24)
                    painter.drawPixmap(round(x - 12), round(y - 12), 24, 24, pixmap)
                else:
                    painter.setPen(QPen(color or QColor(255, 210, 40), 2))
                    painter.drawEllipse(QPointF(x, y), 5, 5)
            painter.restore()
            if self._status_lines and self._source_size is not None:
                paint_status_panel(painter, self._status_lines, QSize(*self._source_size))
            if self._distance is None and self._target_marker is None:
                return
            show_arrow = (self._target_marker is None) and (not self._nearby)
            if show_arrow:
                tip, left, right = direction_triangle_geometry(
                    self._bearing, self._minimap_box
                )
                polygon = QPolygonF([
                    QPointF(*tip), QPointF(*left), QPointF(*right)
                ])
                # Black outer stroke + bright inner stroke keeps the arrow
                # legible over every minimap terrain colour.
                painter.setPen(QPen(QColor(0, 0, 0, 235), 8,
                                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.setBrush(QColor(255, 45, 45, 245))
                painter.drawPolygon(polygon)
                painter.setPen(QPen(QColor(255, 235, 40, 255), 3,
                                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPolygon(polygon)

            if self._target_marker is not None:
                tx, ty = self._target_marker[:2]
                if self._target_pixmap is not None and not self._target_pixmap.isNull():
                    pw = self._target_pixmap.width()
                    ph = self._target_pixmap.height()
                    painter.drawPixmap(int(tx - pw / 2), int(ty - ph / 2), self._target_pixmap)
                else:
                    painter.setBrush(QColor(255, 210, 40, 220))
                    painter.setPen(QPen(QColor(0, 0, 0, 220), 2))
                    painter.drawEllipse(QPointF(float(tx), float(ty)), 8, 8)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(255, 40, 40), 3))
                painter.drawEllipse(QPointF(float(tx), float(ty)), 18, 18)

            box = self._minimap_box
            banner = QRectF(
                float(box.x), float(box.y + box.height + 8),
                460.0 if self._position_note else 285.0,
                62.0 if self._position_note else 38.0,
            )
            painter.setBrush(QColor(0, 0, 0, 210))
            painter.setPen(QPen(QColor(255, 215, 30), 3))
            painter.drawRoundedRect(banner, 7, 7)
            painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
            painter.setPen(QColor(255, 255, 255))
            if self._nearby:
                text = "目标在附近，请停留等待同步" if self._position_note else "收集目标就在附近，请观察周围"
            else:
                text = f"目标方向  {self._bearing:.0f}°"
                if self._distance is not None:
                    text += f"    距离 {'约 ' if self._position_note else ''}{format_distance_meters(self._distance)}"
            painter.drawText(banner.adjusted(10, 0, -8, -24 if self._position_note else 0),
                             Qt.AlignVCenter | Qt.AlignLeft, text)
            if self._position_note:
                painter.setFont(QFont("Microsoft YaHei UI", 9))
                painter.setPen(QColor(255, 215, 30))
                painter.drawText(banner.adjusted(10, 35, -8, 0),
                                 Qt.AlignVCenter | Qt.AlignLeft, self._position_note)

            # 当前目标的说明和参考图片显示在方向提示下面。
            hint_text = self._guide_description if self._guide_description else self._hint_text
            if hint_text or self._guide_status:
                hint_width = 455.0
                hint_x = float(box.x)
                hint_y = banner.bottom() + 7.0
                body_font = QFont("Microsoft YaHei UI", 10)
                painter.setFont(body_font)
                metrics = painter.fontMetrics()
                body_probe = QRectF(0, 0, hint_width - 22.0, 150.0)
                measured = metrics.boundingRect(
                    body_probe.toRect(),
                    Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                    hint_text,
                )
                body_height = min(126.0, max(22.0, float(measured.height())))
                hint_box = QRectF(
                    hint_x, hint_y, hint_width, body_height + 40.0
                    + (self._guide_pixmap.height() + 12 if self._guide_pixmap is not None else 0)
                    + (24 if self._guide_status in ('loading', 'failed') else 0)
                )
                painter.setBrush(QColor(0, 0, 0, 218))
                painter.setPen(QPen(QColor(255, 215, 30), 2))
                painter.drawRoundedRect(hint_box, 7, 7)
                painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
                painter.setPen(QColor(255, 215, 30))
                painter.drawText(
                    hint_box.adjusted(10, 5, -10, -5),
                    Qt.AlignLeft | Qt.AlignTop,
                    "收集提示",
                )
                painter.setFont(body_font)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(
                    QRectF(
                        hint_box.x() + 10.0, hint_box.y() + 29.0,
                        hint_box.width() - 20.0, body_height + 4.0,
                    ),
                    Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                    hint_text,
                )
                image_y = hint_box.y() + body_height + 36
                if self._guide_pixmap is not None:
                    painter.drawPixmap(int(hint_box.x() + 10), int(image_y), self._guide_pixmap)
                elif self._guide_status in ('loading', 'failed'):
                    painter.drawText(QRectF(hint_box.x() + 10, image_y, hint_width - 20, 24),
                                     Qt.AlignLeft, '参考图片加载中…' if self._guide_status == 'loading' else '参考图片加载失败')
        finally:
            painter.end()


class BigmapLineWindow(MinimapDirectionWindow):
    _routes_requested = Signal(object, object)

    def __init__(self):
        super().__init__()
        self._path_layers = ()
        self._pending_routes = None
        self._routes_requested.connect(self._do_routes, Qt.QueuedConnection)

    def render_routes(self, geometry, layers):
        with self._frame_lock:
            notify = self._pending_routes is None
            self._pending_routes = (tuple(geometry), tuple(layers), self._frame_epoch)
        if notify:
            self._routes_requested.emit(None, None)

    def _do_routes(self, geometry, layers):
        if geometry is None:
            with self._frame_lock:
                pending, self._pending_routes = self._pending_routes, None
            if pending is None:
                return
            geometry, layers, epoch = pending
            if epoch != self._frame_epoch:
                return
        if self.isVisible() and self.geometry().getRect() == tuple(geometry) and self._path_layers == layers:
            return
        if self.geometry().getRect() != tuple(geometry):
            self.setGeometry(*geometry)
        self._path_layers = layers
        if layers:
            if not self.isVisible():
                self.show()
                self.raise_()
            self.update()
        else:
            self.hide()

    def paintEvent(self, event):
        from src.utils.MapItemOverlay import paint_path_layers
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        try:
            painter.setClipRect(self.rect())
            paint_path_layers(painter, self._path_layers, draw_nodes=False)
        finally:
            painter.end()
