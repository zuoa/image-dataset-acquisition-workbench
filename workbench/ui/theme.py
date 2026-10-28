"""A quiet, pastel dashboard skin on top of QFluentWidgets."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsDropShadowEffect
from qfluentwidgets import CardWidget

BACKGROUND = "#F8F9FD"
INK = "#252B43"
MUTED = "#8C91A5"
BLUE = "#3978F6"
TONES = (
    ("#F1EDFF", "#9069EF"),
    ("#FFF3E9", "#F4A66D"),
    ("#EBF9EF", "#70C98F"),
    ("#ECF3FF", "#6291F6"),
)


class SoftCard(CardWidget):
    """Keep Fluent's interactions while using borderless, rounded surfaces."""

    def __init__(self, parent=None, *, tint="#FFFFFF", shadow=True):
        self.tint = QColor(tint)
        super().__init__(parent)
        self.setBorderRadius(20)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(24)
            effect.setOffset(0, 5)
            effect.setColor(QColor(49, 58, 93, 9))
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
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.tint)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 20, 20)


class MetricCard(SoftCard):
    """Small real archive counts with the reference's light contour motif."""

    def __init__(self, label, icon, tone, parent=None):
        super().__init__(parent, tint=tone[0], shadow=False)
        from PySide6.QtWidgets import QVBoxLayout
        from qfluentwidgets import BodyLabel, CaptionLabel

        self.accent = QColor(tone[1])
        self.icon = icon
        self.setMinimumHeight(148)
        self.setMinimumWidth(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 62, 18, 18)
        layout.setSpacing(3)
        self.value_label = BodyLabel("0")
        self.value_label.setObjectName("metricValue")
        self.value_label.setTextColor(QColor(INK), QColor(INK))
        layout.addWidget(self.value_label)
        caption = CaptionLabel(label)
        caption.setTextColor(QColor("#85869B"), QColor("#85869B"))
        layout.addWidget(caption)

    def set_value(self, value):
        self.value_label.setText(f"{value:,}")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(self._clip())
        # Decorative contours echo the source image without implying chart data.
        for index in range(4):
            color = QColor(self.accent)
            color.setAlpha(22 + index * 5)
            painter.setPen(QPen(color, 1.2))
            curve = QPainterPath()
            curve.moveTo(self.width() * 0.52, 58 + index * 8)
            curve.cubicTo(
                self.width() * 0.7,
                13 + index * 8,
                self.width() * 0.9,
                25 + index * 9,
                self.width() + 12,
                8 + index * 8,
            )
            painter.drawPath(curve)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.accent)
        painter.drawRoundedRect(QRectF(20, 18, 34, 34), 10, 10)
        self.icon.render(painter, QRectF(29, 27, 16, 16), fill="#FFFFFF")

    def _clip(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 20, 20)
        return path
