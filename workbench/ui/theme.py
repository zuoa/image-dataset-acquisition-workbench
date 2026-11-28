"""Morandi light design tokens and elevated surface primitives for QFluentWidgets."""

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QSizePolicy, QWidget
from qfluentwidgets import CardWidget

BACKGROUND = "#F4F1EC"
SIDEBAR_BG = "#FAF8F4"
SURFACE = "#FDFCFA"
SURFACE_SUBTLE = "#EFECE6"
SURFACE_HOVER = "#F1EDE6"
SURFACE_TINT = "#E8EEF2"

BORDER = "#E3DDD4"
BORDER_STRONG = "#CBC3B6"
BORDER_SUBTLE = "#EDE8DF"

INK = "#2F3134"
INK_SECONDARY = "#595B60"
MUTED = "#88847D"

# Primary Morandi accent: Dusty Slate Blue (雾霾蓝)
BLUE = "#5F7A91"
BLUE_HOVER = "#506A80"
BLUE_PRESSED = "#42596D"
SOFT_BORDER = "#CEDBE4"
SOFT_HOVER = "#DCE5EC"

# Morandi semantic accents: Dusty Rose (干枯玫瑰红), Warm Ochre (暖姜沙), Dusty Mauve (烟灰紫)
DANGER = "#A85652"
DANGER_BG = "#F6ECEA"
DANGER_HOVER = "#EEDBD8"
DANGER_BORDER = "#DFC1BC"

WARNING = "#967142"
WARNING_BG = "#F4EDE1"
WARNING_BORDER = "#E1D0B7"

CANVAS_DARK = "#242629"
CANVAS_EMPTY = "#EAE6DF"

CANDLING_BG = "#F0ECF2"
CANDLING_BORDER = "#D9CFE0"
CANDLING_ACCENT = "#7A6388"

RADIUS_CARD = 16
RADIUS_CANVAS = 18
RADIUS_CONTROL = 10
RADIUS_INNER = 10

# Harmonious Morandi card accents: Dusty Slate, Terracotta Blush, Sage Green, Sand Taupe
TONES = (
    ("#EAEFF2", "#637D93"),
    ("#F3EAE6", "#AE7B6A"),
    ("#EAEFE9", "#698772"),
    ("#F3EFE6", "#9D8463"),
)

STATUS_STYLES = {
    "neutral": ("#ECE8E1", "#65635D", "#DDD7CD"),
    "success": ("#E8EFEA", "#4D735C", "#C8DACF"),
    "info": ("#E8EFF3", "#4F6B82", "#C9D8E2"),
    "warning": (WARNING_BG, WARNING, WARNING_BORDER),
    "danger": (DANGER_BG, DANGER, DANGER_BORDER),
    "candling": (CANDLING_BG, CANDLING_ACCENT, CANDLING_BORDER),
}


class SoftCard(CardWidget):
    """Crisp, elevated rounded surface with subtle warm hairline border."""

    def __init__(self, parent=None, *, tint=SURFACE, border=BORDER, radius=RADIUS_CARD, shadow=True):
        self.tint = QColor(tint)
        self.border_color = QColor(border) if border else None
        self.radius = radius
        super().__init__(parent)
        self.setBorderRadius(radius)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(18)
            effect.setOffset(0, 4)
            effect.setColor(QColor(47, 49, 52, 10))
            self.setGraphicsEffect(effect)

    def _normalBackgroundColor(self):
        return self.tint

    def _hoverBackgroundColor(self):
        return self.tint

    def _pressedBackgroundColor(self):
        return self.tint

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.border_color is not None:
            painter.setPen(QPen(self.border_color, 1))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.tint)
        painter.drawRoundedRect(rect, self.radius, self.radius)


class StatusPill(QWidget):
    """Compact semantic pill badge with optional leading status dot or icon."""

    def __init__(self, text="", tone="neutral", *, dot=True, icon=None, parent=None):
        super().__init__(parent)
        self._text = text
        self._tone = tone
        self._dot = dot
        self._icon = icon
        self.setProperty("compactControl", True)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(26)

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text
        self.updateGeometry()
        self.update()

    def set_status(self, text, tone="neutral", *, dot=None, icon=None):
        self._text = text
        self._tone = tone if tone in STATUS_STYLES else "neutral"
        if dot is not None:
            self._dot = dot
        if icon is not None:
            self._icon = icon
        self.updateGeometry()
        self.update()

    def _font(self):
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        return font

    def sizeHint(self):
        metrics = QFontMetrics(self._font())
        text_width = metrics.horizontalAdvance(self._text)
        lead_space = 18 if self._icon is not None else (13 if self._dot else 0)
        return QSize(max(36, text_width + lead_space + 20), 26)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        bg, fg, border = STATUS_STYLES.get(self._tone, STATUS_STYLES["neutral"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        x = 10.0
        if self._icon is not None:
            self._icon.render(painter, QRectF(x, (self.height() - 12) / 2, 12, 12), fill=fg)
            x += 17.0
        elif self._dot:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fg))
            painter.drawEllipse(QRectF(x, (self.height() - 6) / 2, 6, 6))
            x += 12.0
        painter.setFont(self._font())
        painter.setPen(QColor(fg))
        painter.drawText(
            QRectF(x, 0, max(0.0, self.width() - x - 10), self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )


class MetricCard(SoftCard):
    """Archive count KPI card with subtle contour motif and optional click action."""

    clicked = Signal()

    def __init__(self, label, icon, tone, parent=None):
        accent = QColor(tone[1])
        border = QColor(accent)
        border.setAlpha(48)
        super().__init__(parent, tint=tone[0], border=border, shadow=False)
        from PySide6.QtWidgets import QVBoxLayout
        from qfluentwidgets import BodyLabel, CaptionLabel

        self.accent = accent
        self.icon = icon
        self._clickable = False
        self._hovered = False
        self.setMinimumHeight(124)
        self.setMinimumWidth(148)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 56, 16, 14)
        layout.setSpacing(2)
        self.value_label = BodyLabel("0")
        self.value_label.setObjectName("metricValue")
        self.value_label.setTextColor(QColor(INK), QColor(INK))
        layout.addWidget(self.value_label)
        caption = CaptionLabel(label)
        caption.setTextColor(QColor(INK_SECONDARY), QColor(INK_SECONDARY))
        layout.addWidget(caption)

    def set_clickable(self, clickable=True):
        self._clickable = clickable
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def set_value(self, value):
        self.value_label.setText(f"{value:,}")

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._clickable and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        border = QColor(self.accent)
        border.setAlpha(135 if (self._clickable and self._hovered) else 48)
        self.border_color = border
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(self._clip())
        for index in range(4):
            color = QColor(self.accent)
            color.setAlpha(20 + index * 5)
            painter.setPen(QPen(color, 1.15))
            curve = QPainterPath()
            curve.moveTo(self.width() * 0.52, 50 + index * 7)
            curve.cubicTo(
                self.width() * 0.7,
                12 + index * 7,
                self.width() * 0.9,
                22 + index * 8,
                self.width() + 12,
                8 + index * 7,
            )
            painter.drawPath(curve)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        painter.drawRoundedRect(QRectF(18, 15, 32, 32), 10, 10)
        self.icon.render(painter, QRectF(26, 23, 16, 16), fill="#FDFCFA")

    def _clip(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        return path
