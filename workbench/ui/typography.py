"""Editorial typography and Morandi control styling for Fluent widgets."""

import sys

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    SearchLineEdit,
    SubtitleLabel,
    setCustomStyleSheet,
    setFontFamilies,
)

from .theme import (
    BLUE,
    BLUE_HOVER,
    BLUE_PRESSED,
    BORDER,
    BORDER_STRONG,
    DANGER,
    DANGER_BG,
    DANGER_BORDER,
    DANGER_HOVER,
    INK,
    INK_SECONDARY,
    RADIUS_CONTROL,
    SOFT_BORDER,
    SOFT_HOVER,
    SURFACE,
    SURFACE_HOVER,
    SURFACE_SUBTLE,
    SURFACE_TINT,
)


def font_families():
    if sys.platform == "darwin":
        return ["Avenir Next", "PingFang SC"]
    if sys.platform == "win32":
        return ["Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI"]
    return ["Inter", "Noto Sans CJK SC", "DejaVu Sans"]


def interface_font(size=14, weight=QFont.Weight.Normal, *, tracking=0.0):
    font = QFont()
    font.setFamilies(font_families())
    font.setPixelSize(size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    if tracking:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
    return font


class Typography(QObject):
    def eventFilter(self, widget, event):
        if event.type() != QEvent.Type.Polish or not isinstance(widget, QWidget):
            return False
        if widget.property("vannusTypography"):
            return False
        widget.setProperty("vannusTypography", True)
        if widget.property("compactControl"):
            return False

        size, weight, tracking = 14, QFont.Weight.Normal, 0.0
        if isinstance(widget, CaptionLabel):
            size = 12
        elif isinstance(widget, SubtitleLabel):
            size, weight, tracking = 15, QFont.Weight.Medium, 0.15
        if isinstance(widget, QLabel) and widget.objectName() == "pageTitle":
            size, weight, tracking = 24, QFont.Weight.DemiBold, 0.35
        elif isinstance(widget, QLabel) and widget.objectName() == "sectionTitle":
            size, weight, tracking = 13, QFont.Weight.Medium, 0.25
        elif isinstance(widget, QLabel) and widget.objectName() == "metricValue":
            size, weight, tracking = 28, QFont.Weight.DemiBold, -0.3

        if isinstance(widget, (BodyLabel, SubtitleLabel)) and widget.lightColor.name() == "#000000":
            color = INK_SECONDARY if widget.objectName() == "sectionTitle" else INK
            widget.setTextColor(color, color)

        if isinstance(widget, QPushButton):
            size, weight = 13, QFont.Weight.Medium
            fold = widget.property("foldControl")
            if fold:
                css = (
                    "QPushButton { font-size: 13px; font-weight: 500; text-align: left; "
                    f"border: none; border-radius: 8px; background: transparent; color: {INK}; "
                    "padding: 4px 6px; }"
                    f"QPushButton:hover {{ background: {SURFACE_HOVER}; }}"
                )
                setCustomStyleSheet(widget, css, css)
                widget.setMinimumHeight(max(32, widget.minimumHeight()))
            else:
                css = (
                    f"QPushButton {{ font-size: 13px; font-weight: 500; border-radius: {RADIUS_CONTROL}px; "
                    f"background: {SURFACE}; border: 1px solid {BORDER}; color: {INK_SECONDARY}; "
                    "padding: 0 14px; }"
                    f"QPushButton:hover {{ background: {SURFACE_HOVER}; border-color: {BORDER_STRONG}; color: {INK}; }}"
                    f"QPushButton:pressed {{ background: {SURFACE_TINT}; }}"
                    f"QPushButton:disabled {{ background: {SURFACE_SUBTLE}; border-color: #E7E2DA; color: #B4AFA7; }}"
                )
                if isinstance(widget, PrimaryPushButton):
                    css += (
                        f"QPushButton {{ background: {BLUE}; border: 1px solid {BLUE}; color: #FDFCFA; }}"
                        f"QPushButton:hover {{ background: {BLUE_HOVER}; border-color: {BLUE_HOVER}; color: #FDFCFA; }}"
                        f"QPushButton:pressed {{ background: {BLUE_PRESSED}; border-color: {BLUE_PRESSED}; color: #FDFCFA; }}"
                        "QPushButton:disabled { background: #DDD8CF; border-color: #DDD8CF; color: #A6A198; }"
                    )
                elif widget.property("dangerAction"):
                    css += (
                        f"QPushButton {{ background: {DANGER_BG}; border: 1px solid {DANGER_BORDER}; color: {DANGER}; }}"
                        f"QPushButton:hover {{ background: {DANGER_HOVER}; border-color: #D2ABA5; color: {DANGER}; }}"
                        f"QPushButton:disabled {{ background: {SURFACE_SUBTLE}; border-color: #E7E2DA; color: #B6B1A9; }}"
                    )
                elif widget.property("softAction"):
                    css += (
                        f"QPushButton {{ background: {SURFACE_TINT}; color: {BLUE}; border: 1px solid {SOFT_BORDER}; }}"
                        f"QPushButton:hover {{ background: {SOFT_HOVER}; border-color: #B9CBD8; color: {BLUE_HOVER}; }}"
                        f"QPushButton:disabled {{ background: {SURFACE_SUBTLE}; border-color: #E7E2DA; color: #B4AFA7; }}"
                    )
                setCustomStyleSheet(widget, css, css)
                widget.setMinimumHeight(max(36, widget.minimumHeight()))
        elif isinstance(widget, QToolButton) and widget.property("vannusIconButton"):
            css = (
                f"QToolButton {{ border-radius: {RADIUS_CONTROL}px; background: {SURFACE}; "
                f"border: 1px solid {BORDER}; }}"
                f"QToolButton:hover {{ background: {SURFACE_HOVER}; border-color: {BORDER_STRONG}; }}"
                f"QToolButton:pressed {{ background: {SURFACE_TINT}; }}"
                f"QToolButton:disabled {{ background: {SURFACE_SUBTLE}; border-color: #E7E2DA; }}"
            )
            if widget.property("softAction"):
                css += (
                    f"QToolButton {{ background: {SURFACE_TINT}; border: 1px solid {SOFT_BORDER}; }}"
                    f"QToolButton:hover {{ background: {SOFT_HOVER}; border-color: #B9CBD8; }}"
                )
            elif widget.property("dangerAction"):
                css += (
                    f"QToolButton {{ background: {DANGER_BG}; border: 1px solid {DANGER_BORDER}; }}"
                    f"QToolButton:hover {{ background: {DANGER_HOVER}; border-color: #D2ABA5; }}"
                    f"QToolButton:disabled {{ background: {SURFACE_SUBTLE}; border-color: #E7E2DA; }}"
                )
            setCustomStyleSheet(widget, css, css)
        elif isinstance(widget, QAbstractSpinBox):
            widget.setFixedHeight(36)
        elif isinstance(widget, QLineEdit) and not isinstance(widget.parentWidget(), QAbstractSpinBox):
            widget.setFixedHeight(36)
            right_pad = 36 if isinstance(widget, SearchLineEdit) else 10
            css = (
                f"QLineEdit {{ background: {SURFACE}; border: 1px solid {BORDER}; "
                f"border-radius: {RADIUS_CONTROL}px; color: {INK}; padding: 0 {right_pad}px 0 11px; }}"
                f"QLineEdit:hover {{ border-color: {BORDER_STRONG}; }}"
                f"QLineEdit:focus {{ border: 1px solid {BLUE}; }}"
            )
            setCustomStyleSheet(widget, css, css)
        widget.setFont(interface_font(size, weight, tracking=tracking))
        return False


def install_typography():
    app = QApplication.instance()
    if app is None or hasattr(app, "vannusTypography"):
        return
    setFontFamilies(font_families())
    app.setFont(interface_font())
    app.vannusTypography = Typography(app)
    app.installEventFilter(app.vannusTypography)
