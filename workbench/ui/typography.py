"""Readable, restrained typography for Fluent controls, including dynamic dialogs."""

import sys

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    SubtitleLabel,
    setCustomStyleSheet,
    setFontFamilies,
)

from .theme import BLUE, INK


def font_families():
    if sys.platform == "darwin":
        return ["Helvetica Neue", "PingFang SC"]
    if sys.platform == "win32":
        return ["Microsoft YaHei UI", "Segoe UI"]
    return ["Noto Sans CJK SC", "DejaVu Sans"]


def interface_font(size=17, weight=QFont.Weight.Normal):
    font = QFont()
    font.setFamilies(font_families())
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


class Typography(QObject):
    def eventFilter(self, widget, event):
        if event.type() != QEvent.Type.Polish or not isinstance(widget, QWidget):
            return False
        if widget.property("vannusTypography"):
            return False
        widget.setProperty("vannusTypography", True)
        size, weight = 17, QFont.Weight.Normal
        if isinstance(widget, CaptionLabel):
            size = 14
        elif isinstance(widget, SubtitleLabel):
            size, weight = 20, QFont.Weight.Medium
        if isinstance(widget, QLabel) and widget.objectName() == "pageTitle":
            size, weight = 28, QFont.Weight.Medium
        elif isinstance(widget, QLabel) and widget.objectName() == "sectionTitle":
            size, weight = 17, QFont.Weight.Medium
        elif isinstance(widget, QLabel) and widget.objectName() == "metricValue":
            size, weight = 30, QFont.Weight.Medium
        if isinstance(widget, (BodyLabel, SubtitleLabel)) and widget.lightColor.name() == "#000000":
            widget.setTextColor(INK, INK)
        if isinstance(widget, QPushButton):
            size = 16
            # Fluent's button stylesheet specifies 14px, so changing QApplication's
            # font alone has no effect. Append through its supported theme API.
            fold = widget.property("foldControl")
            alignment = "text-align: left; border: none; background: transparent;" if fold else ""
            css = (
                "QPushButton { font: 16px '"
                + font_families()[0]
                + "'; font-weight: 400; border-radius: 11px; "
                "background: #FFFFFF; border: 1px solid #E9ECF4; color: #505971; " + alignment + "}"
                "QPushButton:hover {background: #F4F7FF; border-color: #DFE7FD;}"
            )
            if isinstance(widget, PrimaryPushButton):
                css += (
                    f"QPushButton {{background: {BLUE}; border: 1px solid {BLUE}; color: white;}}"
                    "QPushButton:hover {background: #2E6CE8; border-color: #2E6CE8;}"
                    "QPushButton:disabled {background: #E7EBF5; border-color: #E7EBF5; color: #A8AFC1;}"
                )
            elif widget.property("softAction"):
                css += "QPushButton {background: #EDF3FF; color: #3978F6; border-color: #EDF3FF;}"
            setCustomStyleSheet(widget, css, css)
            widget.setMinimumHeight(max(40, widget.minimumHeight()))
        elif isinstance(widget, QAbstractSpinBox):
            widget.setFixedHeight(40)
        elif isinstance(widget, QLineEdit) and not isinstance(widget.parentWidget(), QAbstractSpinBox):
            widget.setFixedHeight(40)
            css = (
                "QLineEdit {background: #FFFFFF; border: 1px solid #EBEDF5; border-radius: 11px;}"
                "QLineEdit:focus {border: 1px solid #A9C3FF;}"
            )
            setCustomStyleSheet(widget, css, css)
        widget.setFont(interface_font(size, weight))
        return False


def install_typography():
    app = QApplication.instance()
    if app is None or hasattr(app, "vannusTypography"):
        return
    setFontFamilies(font_families())
    app.setFont(interface_font())
    app.vannusTypography = Typography(app)
    app.installEventFilter(app.vannusTypography)
