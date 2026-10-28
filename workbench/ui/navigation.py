"""Fluent navigation items with a softer, pill-shaped dashboard appearance."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtSvg import QSvgRenderer
from qfluentwidgets import NavigationPushButton, NavigationWidget

from ..branding import ICON_PATH, NAME
from .theme import BLUE, INK
from .typography import interface_font


class SidebarBrand(NavigationWidget):
    def __init__(self, parent=None):
        super().__init__(False, parent)
        self.logo = QSvgRenderer(str(ICON_PATH), self)
        self.setAccessibleName(f"{NAME} 图像采集工作台")
        self.setCompacted(True)

    def setCompacted(self, compact):
        self.isCompacted = compact
        self.setFixedSize(40 if compact else self.EXPAND_WIDTH, 130)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        self.logo.render(painter, QRectF(3 if self.isCompacted else 8, 51, 34, 34))
        if not self.isCompacted:
            font = interface_font(23, QFont.Weight.Medium)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.2)
            painter.setFont(font)
            painter.setPen(QColor(INK))
            painter.drawText(QRectF(53, 51, self.width() - 57, 34), Qt.AlignmentFlag.AlignVCenter, NAME)
            painter.setFont(interface_font(13))
            painter.setPen(QColor("#969BAD"))
            painter.drawText(
                QRectF(12, 92, self.width() - 18, 24), Qt.AlignmentFlag.AlignVCenter, "图像采集工作台"
            )


class SidebarItem(NavigationPushButton):
    def __init__(self, icon, text, parent=None):
        super().__init__(icon, text, True, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)

    def setCompacted(self, compact):
        self.isCompacted = compact
        self.setFixedSize(40 if compact else self.EXPAND_WIDTH, 48)
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
            painter.setBrush(QColor("#EDF2FF" if self.isSelected else "#F7F8FC"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 2, -1, -2), 16, 16)
        color = BLUE if self.isSelected else "#9A9EB1"
        self._icon.render(painter, QRectF(12, 15, 18, 18), fill=color)
        if not self.isCompacted:
            painter.setFont(interface_font(16))
            painter.setPen(QColor(BLUE if self.isSelected else "#9297AA"))
            painter.drawText(
                QRectF(47, 0, self.width() - 56, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text()
            )
