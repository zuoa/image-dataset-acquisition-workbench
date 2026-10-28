from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon as FIF


class ImageCanvas(QWidget):
    regions_changed = Signal(list)
    dimensions_changed = Signal(int, int)

    def __init__(self, parent=None, editable=False):
        super().__init__(parent)
        self.setMinimumSize(280, 220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.image = QImage()
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
        self.setToolTip(
            "滚轮缩放 · 双击适应窗口" + (" · 拖动绘制热区 · 选中后 Delete 删除" if editable else "")
        )

    def set_image(self, image):
        previous = self.image.size()
        self.image = image
        if previous != image.size():
            self.zoom = 1.0
            self.pan = QPointF()
            # Never silently adapt a template to another resolution.
            if self.regions:
                self.regions = []
                self.selected = -1
                self.regions_changed.emit([])
            self.dimensions_changed.emit(image.width(), image.height())
        self.update()

    def set_regions(self, regions):
        self.regions = [dict(r) for r in regions]
        self.selected = -1
        self.regions_changed.emit(self.regions)
        self.update()

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        scale = min(self.width() / self.image.width(), self.height() / self.image.height()) * self.zoom
        width, height = self.image.width() * scale, self.image.height() * scale
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
            max(0, min(self.image.width(), (point.x() - rect.x()) * self.image.width() / rect.width())),
            max(0, min(self.image.height(), (point.y() - rect.y()) * self.image.height() / rect.height())),
        )

    def screen_rect(self, region):
        rect = self.image_rect()
        scale = rect.width() / self.image.width()
        return QRectF(
            rect.x() + region["x"] * scale,
            rect.y() + region["y"] * scale,
            region["w"] * scale,
            region["h"] * scale,
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 22, 22)
        p.setClipPath(clip)
        p.fillPath(clip, QColor("#F0F3FC" if self.image.isNull() else "#202638"))
        if self.image.isNull():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            badge = QRectF(self.width() / 2 - 34, self.height() / 2 - 98, 68, 68)
            p.drawRoundedRect(badge, 22, 22)
            FIF.CAMERA.render(p, badge.adjusted(20, 20, -20, -20), fill="#6F91E8")
            p.setPen(QColor("#65718F"))
            font = QFont(self.font())
            font.setPixelSize(23)
            font.setWeight(QFont.Weight.Medium)
            p.setFont(font)
            p.drawText(
                self.rect().adjusted(20, 8, -20, 8), Qt.AlignmentFlag.AlignCenter, "连接相机，开始采集"
            )
            font.setPixelSize(15)
            font.setWeight(QFont.Weight.Normal)
            p.setFont(font)
            p.setPen(QColor("#9BA6BF"))
            p.drawText(
                self.rect().adjusted(20, 66, -20, 66),
                Qt.AlignmentFlag.AlignCenter,
                "在右侧选择相机，点击连接",
            )
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self.image_rect(), self.image)
        for index, region in enumerate(self.regions if self.show_regions else []):
            rect = self.screen_rect(region)
            color = QColor("#45D5D3" if index == self.selected else "#A3EEE9")
            p.setPen(QPen(color, 2))
            p.setBrush(QColor(32, 176, 184, 24))
            p.drawRect(rect)
            label = f"{region['name']}  ·  {region['w']} × {region['h']}"
            fm = p.fontMetrics()
            label_rect = QRectF(
                rect.x(), max(self.image_rect().y(), rect.y() - 26), fm.horizontalAdvance(label) + 18, 24
            )
            p.fillRect(label_rect, QColor("#087F9C"))
            p.setPen(Qt.GlobalColor.white)
            p.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
            if self.editable and index == self.selected:
                p.fillRect(rect.right() - 4, rect.bottom() - 4, 8, 8, color)
        if self.drag and self.drag[0] == "draw" and self.start:
            p.setPen(QPen(QColor("#45D5D3"), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(32, 176, 184, 32))
            p.drawRect(QRectF(self.start, self.drag[1]).normalized())
        p.end()

    def mousePressEvent(self, event):
        if self.image.isNull():
            return
        self.setFocus()
        point = event.position()
        if event.button() == Qt.MouseButton.MiddleButton or (
            not self.editable and event.button() == Qt.MouseButton.LeftButton
        ):
            self.drag = ("pan", point, QPointF(self.pan))
            return
        if event.button() != Qt.MouseButton.LeftButton or not self.editable:
            return
        if not self.image_rect().contains(point):
            return
        if not self.draw_mode:
            for index in range(len(self.regions) - 1, -1, -1):
                rect = self.screen_rect(self.regions[index])
                if rect.adjusted(-4, -4, 4, 4).contains(point):
                    self.selected = index
                    self.original_region = dict(self.regions[index])
                    mode = (
                        "resize"
                        if math.hypot(point.x() - rect.right(), point.y() - rect.bottom()) < 12
                        else "move"
                    )
                    self.start = self.to_image(point)
                    self.drag = (mode,)
                    self.update()
                    return
        self.start = point
        self.drag = ("draw", point)
        self.selected = -1
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            return
        point = event.position()
        mode = self.drag[0]
        if mode == "pan":
            self.pan = self.drag[2] + point - self.drag[1]
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
            if mode == "move":
                region["x"] = round(
                    max(0, min(self.image.width() - old["w"], old["x"] + current.x() - self.start.x()))
                )
                region["y"] = round(
                    max(0, min(self.image.height() - old["h"], old["y"] + current.y() - self.start.y()))
                )
            else:
                region["w"] = round(max(2, min(self.image.width() - region["x"], current.x() - region["x"])))
                region["h"] = round(max(2, min(self.image.height() - region["y"], current.y() - region["y"])))
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag is None:
            return
        if self.drag[0] == "draw":
            a, b = self.to_image(self.start), self.to_image(self.drag[1])
            x, y = math.floor(min(a.x(), b.x())), math.floor(min(a.y(), b.y()))
            w, h = math.floor(abs(a.x() - b.x())), math.floor(abs(a.y() - b.y()))
            if w >= 2 and h >= 2:
                number = 1
                names = {r["name"] for r in self.regions}
                while f"热区 {number}" in names:
                    number += 1
                self.regions.append(dict(name=f"热区 {number}", x=x, y=y, w=w, h=h))
                self.selected = len(self.regions) - 1
        self.drag = None
        self.start = None
        self.regions_changed.emit(self.regions)
        self.update()

    def keyPressEvent(self, event):
        if self.editable and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected >= 0:
            self.regions.pop(self.selected)
            self.selected = -1
            self.regions_changed.emit(self.regions)
            self.update()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        self.zoom = max(1, min(8, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        if self.zoom == 1:
            self.pan = QPointF()
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.zoom = 1
        self.pan = QPointF()
        self.update()
