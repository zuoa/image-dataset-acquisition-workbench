"""Paged media models with bounded, asynchronous photo thumbnail caching."""

from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate
from qfluentwidgets import FluentIcon as FIF

from .common import video_duration

ROLE = Qt.ItemDataRole.UserRole + 1


class ThumbnailSignals(QObject):
    ready = Signal(str, QImage)


class ThumbnailJob(QRunnable):
    def __init__(self, path, cache, signals):
        super().__init__()
        self.path, self.cache, self.signals = path, cache, signals

    def run(self):
        try:
            target = self.cache / (self.path.parent.name + ".jpg")
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
            return row["sample_id"] or row["id"][:10].upper()
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{row['sample_id'] or row['id']}\n{row['batch_name']} · {row['width']} × {row['height']}\n{row['note']}"
        if role == Qt.ItemDataRole.DecorationRole:
            path = str(self.workspace.path(row["original"]))
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
            if str(self.workspace.path(row["original"])) == path:
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
        self.card_width = 230
        self.compact = compact

    def sizeHint(self, option, index):
        return QSize(self.card_width, 126 if self.compact else round(self.card_width * 0.68) + 77)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setBrush(QColor("#EDF3FF" if selected else "#FFFFFF"))
        painter.setPen(
            QPen(QColor("#3978F6" if selected else "#D6E2FE" if hovered else "#F0F1F7"), 2 if selected else 1)
        )
        painter.drawRoundedRect(rect, 16, 16)
        image_rect = rect.adjusted(7, 7, -7, -39 if self.compact else -66)
        painter.fillRect(image_rect, QColor("#E8EEF2"))
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if pixmap and not pixmap.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(image_rect, pixmap, pixmap.rect())
        row = index.data(ROLE)
        video = row and row.get("media_type") == "video"
        if video:
            painter.fillRect(image_rect, QColor("#ECF3FF"))
            center = image_rect.center()
            FIF.VIDEO.render(
                painter,
                QRectF(center.x() - 20, center.y() - 20, 40, 40),
                fill="#6291F6",
            )
        if row and row.get("media_type"):
            badge = image_rect.adjusted(8, 8, 0, 0)
            badge.setSize(QSize(44, 24))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(badge, 6, 6)
            font = QFont(option.font)
            font.setPixelSize(12)
            painter.setFont(font)
            painter.setPen(QColor("#3978F6" if video else "#647487"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "视频" if video else "图片")
        if row:
            font = QFont(option.font)
            font.setPixelSize(17)
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            painter.setPen(QColor("#182532"))
            title = row["sample_id"] or row["id"][:10].upper()
            text_rect = rect.adjusted(12, rect.height() - (33 if self.compact else 59), -12, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignTop,
                painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width()),
            )
            if not self.compact:
                font.setPixelSize(14)
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QColor("#647487"))
                dimensions = (
                    video_duration(row["duration_ms"]) if video else f"{row['width']} × {row['height']}"
                )
                subtitle = f"{dimensions}  ·  {row['batch_name']}"
                painter.drawText(
                    rect.adjusted(12, rect.height() - 34, -12, 0),
                    Qt.AlignmentFlag.AlignTop,
                    painter.fontMetrics().elidedText(
                        subtitle, Qt.TextElideMode.ElideRight, rect.width() - 24
                    ),
                )
        painter.restore()
