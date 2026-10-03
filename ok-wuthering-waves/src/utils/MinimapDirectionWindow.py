"""Click-through Qt overlay for the selected chest's minimap direction.

The native ok GDI overlay can be created without an owner window and, on some
Windows/DPI combinations, remains behind the game even though its render
callback succeeds. This small Qt window uses the same proven transparent,
always-on-top approach as the interactive big-map overlay, but is completely
input-transparent. It performs no capture or input of its own.
"""

from __future__ import annotations

import ctypes
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


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

    _frame_requested = Signal(
        object, object, object, object, object, object, object
    )
    _hide_requested = Signal()

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
        self._nearby = False
        self._hint_text = ""
        self._no_activate_applied = False
        self._last_frame = None
        self._frame_requested.connect(self._do_frame, Qt.QueuedConnection)
        self._hide_requested.connect(self._do_hide, Qt.QueuedConnection)

    def render_direction(self, geometry, bearing, minimap_box, distance=None,
                         target_marker=None, nearby=False, hint_text=""):
        self._frame_requested.emit(
            tuple(geometry), float(bearing), minimap_box,
            None if distance is None else float(distance),
            None if target_marker is None else tuple(target_marker), bool(nearby),
            str(hint_text or ""),
        )

    def hide_overlay(self):
        self._hide_requested.emit()

    def _do_frame(self, geometry, bearing, minimap_box, distance, target_marker,
                  nearby, hint_text):
        geometry = tuple(int(value) for value in geometry)
        frame_key = (
            geometry, float(bearing), minimap_box.x, minimap_box.y,
            minimap_box.width, minimap_box.height, distance, target_marker,
            bool(nearby), str(hint_text or ""),
        )
        if self.isVisible() and frame_key == self._last_frame:
            return
        self._last_frame = frame_key
        if self.geometry().getRect() != geometry:
            self.setGeometry(*geometry)
        self._bearing = float(bearing)
        self._minimap_box = minimap_box
        self._distance = distance
        self._target_marker = target_marker
        self._nearby = bool(nearby)
        self._hint_text = str(hint_text or "")
        if not self.isVisible():
            self.show()
            self.raise_()
        self.update()

    def _do_hide(self):
        self._last_frame = None
        self._bearing = None
        self._target_marker = None
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
            if not self._nearby:
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
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(255, 40, 40), 4))
                painter.drawEllipse(QPointF(float(tx), float(ty)), 21, 21)

            box = self._minimap_box
            banner = QRectF(
                float(box.x), float(box.y + box.height + 8), 285.0, 38.0
            )
            painter.setBrush(QColor(0, 0, 0, 210))
            painter.setPen(QPen(QColor(255, 215, 30), 3))
            painter.drawRoundedRect(banner, 7, 7)
            painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
            painter.setPen(QColor(255, 255, 255))
            if self._nearby:
                text = "收集目标就在附近，请观察周围"
            else:
                text = f"目标方向  {self._bearing:.0f}°"
                if self._distance is not None:
                    display_distance = int(round(self._distance / 50.0) * 50)
                    text += f"    距离 {display_distance}"
            painter.drawText(banner.adjusted(10, 0, -8, 0),
                             Qt.AlignVCenter | Qt.AlignLeft, text)

            # This is the same location.description shown when the user clicks
            # the selected marker on the big map.  Keep it close to the stable
            # direction/distance banner so manual navigation and the opening
            # method can be read together.  The window remains fully
            # click-through; this panel adds no interaction or input handling.
            if self._hint_text:
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
                    self._hint_text,
                )
                body_height = min(126.0, max(22.0, float(measured.height())))
                hint_box = QRectF(
                    hint_x, hint_y, hint_width, body_height + 40.0
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
                    self._hint_text,
                )
        finally:
            painter.end()
