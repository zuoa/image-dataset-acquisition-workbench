from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget
from qfluentwidgets import FluentIcon as FIF

from .theme import BLUE, BORDER, CANVAS_DARK, CANVAS_EMPTY, INK_SECONDARY, MUTED, RADIUS_CANVAS, SURFACE
from .typography import interface_font

_HANDLE_RADIUS = 10.0


class ImageCanvas(QWidget):
    regions_changed = Signal(list)
    dimensions_changed = Signal(int, int)
    selection_changed = Signal(int)
    stop_interval_requested = Signal()

    def __init__(self, parent=None, editable=False):
        super().__init__(parent)
        self.setMinimumSize(280, 220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.image = QImage()
        self.source_size = QSize()
        self.regions = []
        self.show_regions = True
        self.editable = editable
        self.draw_mode = False
        self.selected = -1
        self.zoom = 1.0
        self.pan = QPointF()
        self.start = None
        self.drag = None
        self.original_region = None
        self._flash_alpha = 0.0
        self._countdown = 0
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(24)
        self._flash_timer.timeout.connect(self._tick_flash)

        # Floating top-center interval capture HUD.
        self.interval_hud = QFrame(self)
        self.interval_hud.setProperty("compactControl", True)
        self.interval_hud.setStyleSheet(
            "QFrame { background: rgba(36, 38, 41, 224); border: 1px solid rgba(253, 252, 250, 34); "
            "border-radius: 18px; }"
        )
        hud_layout = QHBoxLayout(self.interval_hud)
        hud_layout.setContentsMargins(14, 6, 8, 6)
        hud_layout.setSpacing(10)
        self._interval_dot = QLabel("●", self.interval_hud)
        self._interval_dot.setProperty("compactControl", True)
        self._interval_dot.setStyleSheet(
            "QLabel { color: #9BB5C9; background: transparent; border: none; font-size: 11px; }"
        )
        hud_layout.addWidget(self._interval_dot)
        self._interval_label = QLabel("", self.interval_hud)
        self._interval_label.setProperty("compactControl", True)
        self._interval_label.setStyleSheet(
            "QLabel { color: #F5F3EE; background: transparent; border: none; font-size: 12px; font-weight: 500; }"
        )
        hud_layout.addWidget(self._interval_label)
        self.stop_interval_button = QPushButton("停止采集", self.interval_hud)
        self.stop_interval_button.setProperty("compactControl", True)
        self.stop_interval_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_interval_button.setStyleSheet(
            "QPushButton { background: rgba(168, 86, 82, 228); color: #FDFCFA; border: none; "
            "border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 500; }"
            "QPushButton:hover { background: rgba(184, 98, 94, 242); }"
        )
        self.stop_interval_button.clicked.connect(self.stop_interval_requested.emit)
        hud_layout.addWidget(self.stop_interval_button)
        self.interval_hud.hide()

        # Floating bottom-right zoom pill.
        self.zoom_badge = QPushButton("", self)
        self.zoom_badge.setProperty("compactControl", True)
        self.zoom_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_badge.setToolTip("点击重置缩放")
        self.zoom_badge.setStyleSheet(
            "QPushButton { background: rgba(36, 38, 41, 212); color: #F5F3EE; "
            "border: 1px solid rgba(253, 252, 250, 34); border-radius: 13px; "
            "padding: 4px 11px; font-size: 12px; font-weight: 500; }"
            "QPushButton:hover { background: rgba(52, 55, 60, 236); }"
        )
        self.zoom_badge.clicked.connect(self.reset_view)
        self.zoom_badge.hide()

        self.setToolTip(
            "滚轮缩放 · 双击适应窗口 · Alt + 拖动平移"
            + (" · 拖动绘制或调整热区 · Delete 删除" if editable else "")
        )

    def _set_selected(self, index):
        if index != self.selected:
            self.selected = index
            self.selection_changed.emit(index)

    def select_region(self, index):
        clamped = index if 0 <= index < len(self.regions) else -1
        if clamped != self.selected:
            self.selected = clamped
            self.update()

    def reset_view(self):
        self.zoom = 1.0
        self.pan = QPointF()
        self._update_overlays()
        self.update()

    def trigger_flash(self):
        self._flash_alpha = 0.52
        self._flash_timer.start()
        self.update()

    def _tick_flash(self):
        self._flash_alpha = max(0.0, self._flash_alpha - 0.085)
        if self._flash_alpha <= 0.0:
            self._flash_timer.stop()
        self.update()

    def set_countdown(self, seconds):
        value = max(0, int(seconds))
        if value != self._countdown:
            self._countdown = value
            self.update()

    def set_interval_status(self, active, remaining_s=0, shot_count=0):
        if active:
            self._interval_label.setText(f"定时采集中 · 已拍 {shot_count} 张 · 下次 {max(1, int(remaining_s))}s")
            self.interval_hud.adjustSize()
            self.interval_hud.show()
            self.interval_hud.raise_()
        else:
            self.interval_hud.hide()
        self._position_overlays()

    def _update_overlays(self):
        show_zoom = not self.image.isNull() and self.zoom > 1.01
        if show_zoom:
            self.zoom_badge.setText(f"{round(self.zoom * 100)}% · 重置")
            self.zoom_badge.adjustSize()
            self.zoom_badge.show()
            self.zoom_badge.raise_()
        else:
            self.zoom_badge.hide()
        self._position_overlays()

    def _position_overlays(self):
        if self.interval_hud.isVisible():
            self.interval_hud.adjustSize()
            x = max(12, (self.width() - self.interval_hud.width()) // 2)
            self.interval_hud.move(x, 14)
        if self.zoom_badge.isVisible():
            self.zoom_badge.adjustSize()
            self.zoom_badge.move(
                max(12, self.width() - self.zoom_badge.width() - 14),
                max(12, self.height() - self.zoom_badge.height() - 14),
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_overlays()

    def set_image(self, image, source_size=None):
        previous = self.source_size
        self.image = image
        self.source_size = QSize(source_size) if source_size is not None else image.size()
        if previous != self.source_size:
            self.zoom = 1.0
            self.pan = QPointF()
            # Never silently adapt a template to another resolution.
            if self.regions:
                self.regions = []
                self._set_selected(-1)
                self.regions_changed.emit([])
            self.dimensions_changed.emit(self.source_size.width(), self.source_size.height())
        self._update_overlays()
        self.update()

    def set_regions(self, regions):
        self.regions = [dict(r) for r in regions]
        self._set_selected(-1)
        self.regions_changed.emit(self.regions)
        self.update()

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        scale = (
            min(self.width() / self.source_size.width(), self.height() / self.source_size.height())
            * self.zoom
        )
        width, height = self.source_size.width() * scale, self.source_size.height() * scale
        return QRectF(
            (self.width() - width) / 2 + self.pan.x(),
            (self.height() - height) / 2 + self.pan.y(),
            width,
            height,
        )

    def to_image(self, point):
        rect = self.image_rect()
        if rect.isEmpty():
            return QPointF()
        return QPointF(
            max(
                0,
                min(
                    self.source_size.width(), (point.x() - rect.x()) * self.source_size.width() / rect.width()
                ),
            ),
            max(
                0,
                min(
                    self.source_size.height(),
                    (point.y() - rect.y()) * self.source_size.height() / rect.height(),
                ),
            ),
        )

    def screen_rect(self, region):
        rect = self.image_rect()
        scale = rect.width() / self.source_size.width()
        return QRectF(
            rect.x() + region["x"] * scale,
            rect.y() + region["y"] * scale,
            region["w"] * scale,
            region["h"] * scale,
        )

    def _hit_handle(self, point, rect):
        corners = (
            ("resize_tl", rect.topLeft()),
            ("resize_tr", rect.topRight()),
            ("resize_bl", rect.bottomLeft()),
            ("resize_br", rect.bottomRight()),
        )
        for name, corner in corners:
            if math.hypot(point.x() - corner.x(), point.y() - corner.y()) <= _HANDLE_RADIUS:
                return name
        return None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), RADIUS_CANVAS, RADIUS_CANVAS)
        p.setClipPath(clip)
        p.fillPath(clip, QColor(CANVAS_EMPTY if self.image.isNull() else CANVAS_DARK))
        if self.image.isNull():
            p.setPen(QPen(QColor(BORDER), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.6, 0.6, -0.6, -0.6), RADIUS_CANVAS, RADIUS_CANVAS)
            p.setPen(QPen(QColor(BORDER), 1))
            p.setBrush(QColor(SURFACE))
            badge = QRectF(self.width() / 2 - 30, self.height() / 2 - 76, 60, 60)
            p.drawRoundedRect(badge, 18, 18)
            FIF.CAMERA.render(p, badge.adjusted(18, 18, -18, -18), fill=BLUE)
            p.setPen(QColor(INK_SECONDARY))
            p.setFont(interface_font(17, QFont.Weight.Medium, tracking=0.2))
            p.drawText(
                QRectF(20, self.height() / 2 - 2, self.width() - 40, 30),
                Qt.AlignmentFlag.AlignCenter,
                "连接相机，开始采集",
            )
            p.setFont(interface_font(13))
            p.setPen(QColor(MUTED))
            p.drawText(
                QRectF(20, self.height() / 2 + 28, self.width() - 40, 24),
                Qt.AlignmentFlag.AlignCenter,
                "在右侧选择摄像头并点击连接，即可预览画面并标定热区",
            )
            p.end()
            return

        rect = self.image_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(rect, self.image)
        if self.show_regions:
            p.setFont(interface_font(12, QFont.Weight.Medium))
            for index, region in enumerate(self.regions):
                box = self.screen_rect(region)
                selected = index == self.selected
                p.setPen(QPen(QColor("#8EC4B8" if selected else "#B8D6CE"), 2 if selected else 1.5))
                p.setBrush(QColor(111, 165, 154, 36 if selected else 22))
                p.drawRect(box)
                label_text = f"{region['name']}  {region['w']}×{region['h']}" if selected else region["name"]
                text_w = p.fontMetrics().horizontalAdvance(label_text)
                pill_y = box.y() - 24 if box.y() >= 28 else box.y() + 4
                pill = QRectF(box.x(), pill_y, max(54, text_w + 14), 21)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#4F7E76" if selected else "rgba(79, 126, 118, 206)"))
                p.drawRoundedRect(pill, 6, 6)
                p.setPen(QColor("#FDFCFA"))
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter, label_text)
                if selected and self.editable:
                    p.setPen(QPen(QColor("#8EC4B8"), 1.8))
                    p.setBrush(QColor("#FDFCFA"))
                    for corner in (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight()):
                        p.drawRoundedRect(QRectF(corner.x() - 4, corner.y() - 4, 8, 8), 2, 2)

        if self.drag and self.drag[0] == "draw":
            p.setPen(QPen(QColor("#8EC4B8"), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(111, 165, 154, 34))
            p.drawRect(QRectF(self.start, self.drag[1]).normalized())

        if self._countdown > 0:
            card = QRectF(self.width() / 2 - 96, self.height() / 2 - 66, 192, 132)
            p.setPen(QPen(QColor(255, 255, 255, 42), 1))
            p.setBrush(QColor(16, 20, 34, 198))
            p.drawRoundedRect(card, 20, 20)
            num_font = QFont(self.font())
            num_font.setPixelSize(56)
            num_font.setWeight(QFont.Weight.Bold)
            p.setFont(num_font)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(card.adjusted(0, 10, 0, -34), Qt.AlignmentFlag.AlignCenter, str(self._countdown))
            sub_font = QFont(self.font())
            sub_font.setPixelSize(12)
            sub_font.setWeight(QFont.Weight.Medium)
            p.setFont(sub_font)
            p.setPen(QColor(220, 228, 248))
            p.drawText(card.adjusted(0, 88, 0, -10), Qt.AlignmentFlag.AlignCenter, "秒后拍照 · 点击取消")

        if self._flash_alpha > 0.0:
            wash = QColor(255, 255, 255)
            wash.setAlphaF(self._flash_alpha * 0.55)
            p.fillPath(clip, wash)
            rim = QColor(255, 255, 255)
            rim.setAlphaF(min(1.0, self._flash_alpha * 1.4))
            p.setPen(QPen(rim, 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), RADIUS_CANVAS - 2, RADIUS_CANVAS - 2)

        p.end()

    def _update_hover_cursor(self, point):
        if self.image.isNull():
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if not self.editable:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.zoom > 1.0 else Qt.CursorShape.ArrowCursor)
            return
        if self.draw_mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self.show_regions:
            for index in range(len(self.regions) - 1, -1, -1):
                rect = self.screen_rect(self.regions[index])
                handle = self._hit_handle(point, rect)
                if handle in ("resize_tl", "resize_br"):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                    return
                if handle in ("resize_tr", "resize_bl"):
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                    return
                if rect.adjusted(-4, -4, 4, 4).contains(point):
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    return
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.zoom > 1.0 else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if self.image.isNull():
            return
        self.setFocus()
        point = event.position()
        alt_pan = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if (
            event.button() == Qt.MouseButton.MiddleButton
            or (not self.editable and event.button() == Qt.MouseButton.LeftButton)
            or (alt_pan and event.button() == Qt.MouseButton.LeftButton)
        ):
            self.drag = ("pan", point, QPointF(self.pan))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton or not self.editable:
            return
        if not self.image_rect().contains(point):
            if self.zoom > 1.0 and not self.draw_mode:
                self.drag = ("pan_or_clear", point, QPointF(self.pan), False)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if not self.draw_mode and self.show_regions:
            for index in range(len(self.regions) - 1, -1, -1):
                rect = self.screen_rect(self.regions[index])
                handle = self._hit_handle(point, rect)
                if handle or rect.adjusted(-4, -4, 4, 4).contains(point):
                    self._set_selected(index)
                    self.original_region = dict(self.regions[index])
                    mode = handle or "move"
                    self.start = self.to_image(point)
                    self.drag = (mode,)
                    self.update()
                    return
        if not self.draw_mode and self.zoom > 1.0:
            self.drag = ("pan_or_clear", point, QPointF(self.pan), False)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        self.start = point
        self.drag = ("draw", point)
        self._set_selected(-1)
        self.update()

    def mouseMoveEvent(self, event):
        point = event.position()
        if self.drag is None:
            self._update_hover_cursor(point)
            return
        mode = self.drag[0]
        if mode == "pan":
            self.pan = self.drag[2] + point - self.drag[1]
        elif mode == "pan_or_clear":
            delta = point - self.drag[1]
            moved = self.drag[3] or math.hypot(delta.x(), delta.y()) >= 3.0
            self.drag = (mode, self.drag[1], self.drag[2], moved)
            if moved:
                self.pan = self.drag[2] + delta
        elif mode == "draw":
            rect = self.image_rect()
            self.drag = (
                mode,
                QPointF(
                    max(rect.left(), min(rect.right(), point.x())),
                    max(rect.top(), min(rect.bottom(), point.y())),
                ),
            )
        elif self.selected >= 0:
            current = self.to_image(point)
            region = self.regions[self.selected]
            old = self.original_region
            right = old["x"] + old["w"]
            bottom = old["y"] + old["h"]
            max_w, max_h = self.source_size.width(), self.source_size.height()
            if mode == "move":
                region["x"] = round(max(0, min(max_w - old["w"], old["x"] + current.x() - self.start.x())))
                region["y"] = round(max(0, min(max_h - old["h"], old["y"] + current.y() - self.start.y())))
            elif mode == "resize_br":
                region["w"] = round(max(2, min(max_w - old["x"], current.x() - old["x"])))
                region["h"] = round(max(2, min(max_h - old["y"], current.y() - old["y"])))
            elif mode == "resize_tl":
                nx = round(max(0, min(right - 2, current.x())))
                ny = round(max(0, min(bottom - 2, current.y())))
                region["x"], region["y"] = nx, ny
                region["w"], region["h"] = right - nx, bottom - ny
            elif mode == "resize_tr":
                ny = round(max(0, min(bottom - 2, current.y())))
                region["y"] = ny
                region["h"] = bottom - ny
                region["w"] = round(max(2, min(max_w - old["x"], current.x() - old["x"])))
            elif mode == "resize_bl":
                nx = round(max(0, min(right - 2, current.x())))
                region["x"] = nx
                region["w"] = right - nx
                region["h"] = round(max(2, min(max_h - old["y"], current.y() - old["y"])))
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag is None:
            return
        mode = self.drag[0]
        changed = False
        if mode == "draw":
            a, b = self.to_image(self.start), self.to_image(self.drag[1])
            x, y = math.floor(min(a.x(), b.x())), math.floor(min(a.y(), b.y()))
            w, h = math.floor(abs(a.x() - b.x())), math.floor(abs(a.y() - b.y()))
            if w >= 2 and h >= 2:
                number = 1
                names = {r["name"] for r in self.regions}
                while f"热区 {number}" in names:
                    number += 1
                self.regions.append(dict(name=f"热区 {number}", x=x, y=y, w=w, h=h))
                self._set_selected(len(self.regions) - 1)
                changed = True
        elif mode == "pan_or_clear":
            if not self.drag[3]:
                self._set_selected(-1)
        elif mode != "pan":
            changed = True
        self.drag = None
        self.start = None
        self._update_hover_cursor(event.position())
        if changed:
            self.regions_changed.emit(self.regions)
        self.update()

    def keyPressEvent(self, event):
        if self.editable and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected >= 0:
            self.regions.pop(self.selected)
            self._set_selected(-1)
            self.regions_changed.emit(self.regions)
            self.update()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        if self.image.isNull():
            return
        self.zoom = max(1.0, min(8.0, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        if self.zoom == 1.0:
            self.pan = QPointF()
        self._update_overlays()
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.reset_view()
