"""Fluent navigation items with refined Morandi pill selection and accent indicator."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtSvg import QSvgRenderer
from qfluentwidgets import NavigationPushButton, NavigationWidget

from ..branding import ICON_PATH, NAME
from .theme import BLUE, INK, INK_SECONDARY, MUTED, SURFACE_HOVER, SURFACE_TINT
from .typography import interface_font


class SidebarBrand(NavigationWidget):
    def __init__(self, parent=None):
        super().__init__(False, parent)
        self.logo = QSvgRenderer(str(ICON_PATH), self)
        self.setAccessibleName(f"{NAME} 图像采集工作台")
        self.setCompacted(True)

    def setCompacted(self, compact):
        self.isCompacted = compact
        self.setFixedSize(40 if compact else self.EXPAND_WIDTH, 108)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        self.logo.render(painter, QRectF(5 if self.isCompacted else 10, 40, 30, 30))
        if not self.isCompacted:
            painter.setFont(interface_font(19, QFont.Weight.DemiBold, tracking=0.4))
            painter.setPen(QColor(INK))
            painter.drawText(QRectF(50, 39, self.width() - 56, 32), Qt.AlignmentFlag.AlignVCenter, NAME)
            painter.setFont(interface_font(12, tracking=0.2))
            painter.setPen(QColor(MUTED))
            painter.drawText(
                QRectF(12, 74, self.width() - 20, 20), Qt.AlignmentFlag.AlignVCenter, "图像采集工作台"
            )


class SidebarItem(NavigationPushButton):
    def __init__(self, icon, text, parent=None):
        super().__init__(icon, text, True, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)

    def setCompacted(self, compact):
        self.isCompacted = compact
        self.setFixedSize(40 if compact else self.EXPAND_WIDTH, 42)
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        if self.isSelected or self.isEnter or self.hasFocus():
            painter.setBrush(QColor(SURFACE_TINT if self.isSelected else SURFACE_HOVER))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 11, 11)
        if self.isSelected:
            painter.setBrush(QColor(BLUE))
            painter.drawRoundedRect(QRectF(2, (self.height() - 18) / 2, 3, 18), 1.5, 1.5)
        color = BLUE if self.isSelected else MUTED
        self._icon.render(painter, QRectF(12, (self.height() - 16) / 2, 16, 16), fill=color)
        if not self.isCompacted:
            painter.setFont(
                interface_font(14, QFont.Weight.Medium if self.isSelected else QFont.Weight.Normal)
            )
            painter.setPen(QColor(BLUE if self.isSelected else INK_SECONDARY))
            painter.drawText(
                QRectF(42, 0, self.width() - 50, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text()
            )
