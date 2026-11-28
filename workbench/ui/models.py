"""Paged media models with bounded, asynchronous photo thumbnail caching."""

import hashlib
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QStyle, QStyledItemDelegate
from qfluentwidgets import FluentIcon as FIF

from ..storage import photo_preview
from .common import video_duration
from .theme import (
    BLUE,
    BORDER,
    BORDER_STRONG,
    CANVAS_DARK,
    CANVAS_EMPTY,
    INK,
    INK_SECONDARY,
    MUTED,
    RADIUS_CARD,
    RADIUS_INNER,
    SURFACE,
    SURFACE_HOVER,
    SURFACE_TINT,
)
from .typography import interface_font

ROLE = Qt.ItemDataRole.UserRole + 1


def media_title(row):
    if row["sample_id"]:
        return row["sample_id"]
    if row.get("media_type") == "video" or Path(row["original"]).stem == "original":
        return row["id"][:10].upper()
    return Path(row["original"]).stem


class ThumbnailSignals(QObject):
    ready = Signal(str, QImage)


class ThumbnailJob(QRunnable):
    def __init__(self, path, cache, signals):
        super().__init__()
        self.path, self.cache, self.signals = path, cache, signals

    def run(self):
        try:
            cache_key = hashlib.sha256(str(self.path).encode("utf-8")).hexdigest()
            target = self.cache / (cache_key + ".jpg")
            if not target.exists():
                with Image.open(self.path) as image:
                    thumb = ImageOps.fit(image.convert("RGB"), (440, 300))
                    thumb.save(target, "JPEG", quality=82)
            image = QImage(str(target))
        except Exception:
            image = QImage()
        self.signals.ready.emit(str(self.path), image)


class PhotoModel(QAbstractListModel):
    total_changed = Signal(int)

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.rows = []
        self.total = 0
        self.filters = {}
        self.cache = OrderedDict()
        self.pending = set()
        self.thumb_signals = ThumbnailSignals(self)
        self.thumb_signals.ready.connect(self._thumbnail_ready)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

    def reload(self, **filters):
        self.beginResetModel()
        self.filters = filters
        self.rows = []
        self.total = self.count_rows(**filters)
        self.endResetModel()
        self.fetchMore(QModelIndex())
        self.total_changed.emit(self.total)

    def count_rows(self, **filters):
        return self.workspace.count_photos(**filters)

    def fetch_rows(self, *, offset, limit, **filters):
        return self.workspace.photos(offset=offset, limit=limit, **filters)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def canFetchMore(self, parent):
        return not parent.isValid() and len(self.rows) < self.total

    def fetchMore(self, parent):
        if parent.isValid() or len(self.rows) >= self.total:
            return
        rows = self.fetch_rows(offset=len(self.rows), limit=100, **self.filters)
        if rows:
            start = len(self.rows)
            self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
            self.rows.extend(rows)
            self.endInsertRows()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == ROLE:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return media_title(row)
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{media_title(row)}\n{row['batch_name']} · {row['width']} × {row['height']}\n{row['note']}"
            )
        if role == Qt.ItemDataRole.DecorationRole:
            path = str(self.workspace.path(photo_preview(row)))
            if path in self.cache:
                self.cache.move_to_end(path)
                return self.cache[path]
            if path not in self.pending:
                self.pending.add(path)
                self.pool.start(
                    ThumbnailJob(Path(path), self.workspace.root / ".thumbnails", self.thumb_signals)
                )
        return None

    @Slot(str, QImage)
    def _thumbnail_ready(self, path, image):
        self.pending.discard(path)
        self.cache[path] = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        while len(self.cache) > 160:
            self.cache.popitem(last=False)
        for i, row in enumerate(self.rows):
            if row.get("media_type") != "video" and str(self.workspace.path(photo_preview(row))) == path:
                self.dataChanged.emit(self.index(i), self.index(i), [Qt.ItemDataRole.DecorationRole])


class MediaModel(PhotoModel):
    def count_rows(self, **filters):
        self.counts = self.workspace.media_counts(**filters)
        return sum(self.counts.values())

    def fetch_rows(self, *, offset, limit, **filters):
        return self.workspace.media(offset=offset, limit=limit, **filters)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and 0 <= index.row() < len(self.rows):
            row = self.rows[index.row()]
            if row["media_type"] == "video":
                if role == Qt.ItemDataRole.DecorationRole:
                    return None
                if role == Qt.ItemDataRole.ToolTipRole:
                    return (
                        f"{row['sample_id'] or row['id']}\n视频 · {video_duration(row['duration_ms'])}"
                        f" · {row['batch_name']}\n{row['note']}"
                    )
        return super().data(index, role)


class PhotoDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.card_width = 220
        self.compact = compact

    def sizeHint(self, option, index):
        return QSize(self.card_width, 118 if self.compact else round(self.card_width * 0.66) + 64)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.setBrush(QColor(SURFACE_TINT if selected else SURFACE_HOVER if hovered else SURFACE))
        painter.setPen(
            QPen(
                QColor(BLUE if selected else BORDER_STRONG if hovered else BORDER),
                2 if selected else 1,
            )
        )
        painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_CARD - 2, RADIUS_CARD - 2)

        image_rect = rect.adjusted(7, 7, -7, -34 if self.compact else -56)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(image_rect), RADIUS_INNER, RADIUS_INNER)

        row = index.data(ROLE)
        video = bool(row and row.get("media_type") == "video")
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)

        painter.save()
        painter.setClipPath(clip)
        if video:
            gradient = QLinearGradient(image_rect.topLeft(), image_rect.bottomLeft())
            gradient.setColorAt(0.0, QColor("#EAEFF2"))
            gradient.setColorAt(1.0, QColor("#DCE4EA"))
            painter.fillRect(image_rect, gradient)
            center = image_rect.center()
            circle = QRectF(center.x() - 22, center.y() - 22, 44, 44)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(253, 252, 250, 224))
            painter.drawEllipse(circle)
            FIF.VIDEO.render(
                painter,
                QRectF(center.x() - 10, center.y() - 10, 20, 20),
                fill=BLUE,
            )
        else:
            painter.fillRect(image_rect, QColor(CANVAS_EMPTY))
            if pixmap and not pixmap.isNull():
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(image_rect, pixmap, pixmap.rect())
        painter.restore()

        if row and row.get("media_type"):
            badge = QRectF(image_rect.x() + 7, image_rect.y() + 7, 38, 21)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(253, 252, 250, 232))
            painter.drawRoundedRect(badge, 6, 6)
            painter.setFont(interface_font(11, QFont.Weight.Medium))
            painter.setPen(QColor(BLUE if video else INK_SECONDARY))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "视频" if video else "图片")

        if video and row:
            dur_text = video_duration(row["duration_ms"])
            painter.setFont(interface_font(11, QFont.Weight.Medium))
            dur_w = painter.fontMetrics().horizontalAdvance(dur_text) + 12
            dur_rect = QRectF(image_rect.right() - dur_w - 6, image_rect.bottom() - 25, dur_w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            dark_pill = QColor(CANVAS_DARK)
            dark_pill.setAlpha(196)
            painter.setBrush(dark_pill)
            painter.drawRoundedRect(dur_rect, 6, 6)
            painter.setPen(QColor("#FDFCFA"))
            painter.drawText(dur_rect, Qt.AlignmentFlag.AlignCenter, dur_text)

        if selected:
            check_rect = QRectF(image_rect.right() - 26, image_rect.top() + 7, 20, 20)
            painter.setPen(QPen(QColor("#FDFCFA"), 1.5))
            painter.setBrush(QColor(BLUE))
            painter.drawEllipse(check_rect)
            pen = QPen(QColor("#FDFCFA"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            cx, cy = check_rect.center().x(), check_rect.center().y()
            painter.drawPolyline([QPointF(cx - 4, cy), QPointF(cx - 1, cy + 3), QPointF(cx + 4.5, cy - 3)])

        if row:
            painter.setFont(interface_font(13 if self.compact else 14, QFont.Weight.Medium))
            painter.setPen(QColor(INK))
            title = media_title(row)
            text_rect = rect.adjusted(10, rect.height() - (28 if self.compact else 49), -10, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignTop,
                painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width()),
            )
            if not self.compact:
                painter.setFont(interface_font(12))
                painter.setPen(QColor(MUTED))
                dimensions = (
                    video_duration(row["duration_ms"]) if video else f"{row['width']} × {row['height']}"
                )
                subtitle = f"{dimensions} · {row['batch_name']}"
                painter.drawText(
                    rect.adjusted(10, rect.height() - 27, -10, 0),
                    Qt.AlignmentFlag.AlignTop,
                    painter.fontMetrics().elidedText(
                        subtitle, Qt.TextElideMode.ElideRight, rect.width() - 20
                    ),
                )
        painter.restore()
