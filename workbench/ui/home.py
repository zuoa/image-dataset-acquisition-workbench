import shutil
from datetime import timezone
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SearchLineEdit,
    SubtitleLabel,
)
from qfluentwidgets import FluentIcon as FIF

from ..timeutils import display_now, format_datetime
from .common import FormDialog, confirm, heading, menu_button, muted
from .models import ROLE, MediaModel, media_title
from .theme import (
    BLUE,
    BORDER,
    BORDER_SUBTLE,
    CANVAS_EMPTY,
    INK,
    MUTED,
    RADIUS_INNER,
    SURFACE,
    SURFACE_HOVER,
    SURFACE_SUBTLE,
    TONES,
    MetricCard,
    SoftCard,
    StatusPill,
)
from .typography import interface_font


class ColorIcon(QWidget):
    def __init__(self, icon, tone, parent=None):
        super().__init__(parent)
        self.icon, self.tone = icon, tone
        self.setFixedSize(40, 40)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.tone[0]))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)
        self.icon.render(painter, QRectF(11, 11, 18, 18), fill=self.tone[1])


class CoverImage(QWidget):
    """Aspect-fill, rounded-clipped dataset cover thumbnail."""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.pixmap = pixmap
        self.setFixedHeight(108)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(rect, RADIUS_INNER, RADIUS_INNER)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor(CANVAS_EMPTY))
        if self.pixmap and not self.pixmap.isNull():
            pw, ph = self.pixmap.width(), self.pixmap.height()
            if pw > 0 and ph > 0:
                scale = max(rect.width() / pw, rect.height() / ph)
                draw_w, draw_h = pw * scale, ph * scale
                target = QRectF(
                    rect.x() + (rect.width() - draw_w) / 2,
                    rect.y() + (rect.height() - draw_h) / 2,
                    draw_w,
                    draw_h,
                )
                painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        painter.restore()
        painter.setPen(QPen(QColor(BORDER_SUBTLE), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, RADIUS_INNER, RADIUS_INNER)


class RecentDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(236, 64)

    def paint(self, painter, option, index):
        row = index.data(ROLE)
        if not row:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SURFACE_HOVER))
            painter.drawRoundedRect(rect.adjusted(0, 2, 0, -2), 10, 10)
        thumb = QRectF(rect.x() + 6, rect.y() + 10, 44, 44)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 10, 10)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(thumb, QColor(SURFACE_SUBTLE))
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if pixmap and not pixmap.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(thumb.toRect(), pixmap, pixmap.rect())
        else:
            icon = FIF.VIDEO if row.get("media_type") == "video" else FIF.PHOTO
            icon.render(painter, thumb.adjusted(13, 13, -13, -13), fill=BLUE)
        painter.restore()

        painter.setFont(interface_font(14, interface_font().Weight.Medium))
        painter.setPen(QColor(INK))
        width = max(20, round(rect.width() - 66))
        title = media_title(row)
        painter.drawText(
            QRectF(rect.x() + 60, rect.y() + 11, width, 21),
            painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, width),
        )
        painter.setFont(interface_font(12))
        painter.setPen(QColor(MUTED))
        date = format_datetime(row["created_at"], "%m/%d %H:%M")
        text = f"{row['dataset_name']} · {date}"
        painter.drawText(
            QRectF(rect.x() + 60, rect.y() + 34, width, 18),
            painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width),
        )
        painter.restore()


class HomePage(QWidget):
    acquire = Signal(str)
    browse = Signal(str)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("datasets")
        self._datasets = []
        self._cover_cache = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(heading("数据集工作台", "连接相机，采集并整理实验图像。"), 1)
        import_button = PushButton(FIF.DOWNLOAD, "导入归档")
        import_button.clicked.connect(app.import_archive)
        header.addWidget(import_button)
        create = PrimaryPushButton(FIF.ADD, "新建数据集")
        create.clicked.connect(self.create_dataset)
        header.addWidget(create)
        layout.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        self.metrics = []
        for title, icon, tone in zip(
            ["数据集", "原始图片", "今日新增", "已归档"], [FIF.FOLDER, FIF.PHOTO, FIF.CAMERA, FIF.SAVE], TONES
        ):
            card = MetricCard(title, icon, tone)
            self.metrics.append(card)
            metrics.addWidget(card, 1)
        self.metrics[0].set_clickable(True)
        self.metrics[0].clicked.connect(lambda: self.search.setFocus())
        self.metrics[1].set_clickable(True)
        self.metrics[1].clicked.connect(self._browse_first_dataset)
        self.metrics[2].set_clickable(True)
        self.metrics[2].clicked.connect(self._browse_first_dataset)
        layout.addLayout(metrics)

        body = QHBoxLayout()
        body.setSpacing(20)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(14)
        row = QHBoxLayout()
        row.addWidget(SubtitleLabel("我的数据集"), 1)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("搜索数据集或标签")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(232)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._render_dataset_grid)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        row.addWidget(self.search)
        ll.addLayout(row)

        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(2, 2, 4, 8)
        self.grid.setSpacing(16)
        scroll.setWidget(self.content)
        ll.addWidget(scroll, 1)
        self.summary = muted("")
        ll.addWidget(self.summary)
        body.addWidget(left, 1)

        aside = QWidget()
        aside.setFixedWidth(280)
        al = QVBoxLayout(aside)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(16)

        recent = SoftCard()
        box = QVBoxLayout(recent)
        box.setContentsMargins(18, 18, 18, 14)
        box.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("最近采集"), 1)
        self.all_recent = PushButton(FIF.VIEW, "全部")
        self.all_recent.clicked.connect(self.browse_recent)
        top.addWidget(self.all_recent)
        box.addLayout(top)
        self.recent_model = MediaModel(app.workspace, self)
        self.recent_view = QListView()
        self.recent_view.setObjectName("gallery")
        self.recent_view.setModel(self.recent_model)
        self.recent_view.setItemDelegate(RecentDelegate(self))
        self.recent_view.setUniformItemSizes(True)
        self.recent_view.setMouseTracking(True)
        self.recent_view.setFixedHeight(272)
        self.recent_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_view.doubleClicked.connect(lambda i: app.show_media(i.data(ROLE)))
        box.addWidget(self.recent_view)
        self.no_recent = muted("还没有采集记录。\n新建一个数据集，开始记录第一张图像。")
        self.no_recent.setMinimumHeight(100)
        box.addWidget(self.no_recent)
        al.addWidget(recent)

        workspace_card = SoftCard(tint="#ECE8E0", border=BORDER, shadow=False)
        box = QVBoxLayout(workspace_card)
        box.setContentsMargins(18, 18, 18, 18)
        box.setSpacing(10)
        top_ws = QHBoxLayout()
        top_ws.setSpacing(10)
        top_ws.addWidget(ColorIcon(FIF.FOLDER, (SURFACE, BLUE)))
        top_ws.addWidget(SubtitleLabel("本地工作区"), 1)
        box.addLayout(top_ws)
        box.addWidget(muted("图像保存在本机，离线也能继续采集。"))
        self.disk_progress = ProgressBar()
        self.disk_progress.setRange(0, 100)
        box.addWidget(self.disk_progress)
        self.free_space = muted("")
        box.addWidget(self.free_space)
        open_workspace = PushButton(FIF.FOLDER, "打开文件夹")
        open_workspace.clicked.connect(lambda: app.open_path(app.workspace.root))
        box.addWidget(open_workspace)
        al.addWidget(workspace_card)
        al.addStretch()

        body.addWidget(aside)
        layout.addLayout(body, 1)
        self.refresh()

    def _browse_first_dataset(self):
        if self._datasets:
            self.browse.emit(self._datasets[0]["id"])

    def browse_recent(self):
        if self.recent_model.rows:
            self.browse.emit(self.recent_model.rows[0]["dataset_id"])

    def create_dataset(self):
        dialog = FormDialog(
            self,
            "新建数据集",
            [("name", "名称", False), ("description", "说明", True), ("tags", "标签（逗号分隔）", False)],
        )
        if dialog.exec():
            dataset_id = self.app.workspace.save_dataset(**dialog.values())
            self.app.refresh()
            self.acquire.emit(dataset_id)

    def edit_dataset(self, data):
        dialog = FormDialog(
            self,
            "编辑数据集",
            [("name", "名称", False), ("description", "说明", True), ("tags", "标签（逗号分隔）", False)],
            data,
        )
        if dialog.exec():
            self.app.workspace.save_dataset(**dialog.values(), dataset_id=data["id"])
            self.app.refresh()

    def delete_dataset(self, data):
        if confirm(
            self,
            "将数据集移入回收站？",
            f"「{data['name']}」及其批次、图片和视频将暂时隐藏，可在回收站恢复。",
            True,
        ):
            self.app.workspace.trash("dataset", [data["id"]])
            self.app.refresh()

    def toggle_archive(self, data):
        self.app.workspace.archive_dataset(data["id"], not data["archived"])
        self.app.refresh()

    def _load_cover(self, rel_path):
        if not rel_path:
            return None
        full_path = Path(self.app.workspace.path(rel_path))
        try:
            key = (str(full_path), full_path.stat().st_mtime_ns)
        except OSError:
            return None
        cached = self._cover_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(str(full_path))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                520, 260, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
        if len(self._cover_cache) > 64:
            self._cover_cache.clear()
        self._cover_cache[key] = pixmap
        return pixmap

    def refresh(self):
        self._datasets = self.app.workspace.datasets()
        datasets = self._datasets
        total = sum(data["count"] for data in datasets)
        midnight = display_now().replace(hour=0, minute=0, second=0, microsecond=0)
        today = self.app.workspace.count_photos(date_from=midnight.astimezone(timezone.utc).isoformat())
        values = [len(datasets), total, today, sum(bool(d["archived"]) for d in datasets)]
        for card, value in zip(self.metrics, values):
            card.set_value(value)
        video_total = sum(data["video_count"] for data in datasets)
        self.summary.setText(
            f"{len(datasets)} 个数据集 · {total:,} 张原图 · {video_total:,} 段视频 · 本地保存"
        )
        usage = shutil.disk_usage(self.app.workspace.root)
        used_pct = round(usage.used / max(usage.total, 1) * 100)
        self.disk_progress.setValue(used_pct)
        self.free_space.setText(
            f"磁盘可用 {usage.free / 1024**3:.1f} GB（已用 {used_pct}%）"
        )
        self.recent_model.reload()
        self.recent_view.setVisible(total + video_total > 0)
        self.all_recent.setVisible(total + video_total > 0)
        self.no_recent.setVisible(total + video_total == 0)
        self._render_dataset_grid()

    def _render_dataset_grid(self):
        datasets = self._datasets
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        search = self.search.text().strip().casefold()
        rows = [d for d in datasets if search in (d["name"] + d["tags"] + d["description"]).casefold()]
        if not rows:
            card = SoftCard()
            box = QVBoxLayout(card)
            box.setContentsMargins(26, 30, 26, 30)
            box.setSpacing(12)
            box.addWidget(ColorIcon(FIF.ADD, TONES[0]))
            box.addWidget(SubtitleLabel("建立第一个数据集" if not datasets else "没有匹配的数据集"))
            box.addWidget(
                muted(
                    "点击右上角「新建数据集」，开始采集实验图像。" if not datasets else "试试其他名称或标签。"
                )
            )
            if not datasets:
                cta = PrimaryPushButton(FIF.ADD, "新建数据集")
                cta.clicked.connect(self.create_dataset)
                box.addWidget(cta, alignment=Qt.AlignmentFlag.AlignLeft)
            self.grid.addWidget(card, 0, 0, 1, 2)
        else:
            for index, data in enumerate(rows):
                self.grid.addWidget(self._card(data, TONES[index % len(TONES)]), index // 2, index % 2)
        for column in range(2):
            self.grid.setColumnStretch(column, 1)
        self.grid.setRowStretch((len(rows) + 1) // 2 + 1, 1)

    def _card(self, data, tone):
        card = SoftCard()
        card.setMinimumWidth(240)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(ColorIcon(FIF.FOLDER, tone))
        title = SubtitleLabel(data["name"])
        title.setWordWrap(True)
        title.setMaximumHeight(48)
        title.setToolTip(data["name"])
        row.addWidget(title, 1)
        row.addWidget(
            menu_button(
                "数据集操作",
                [
                    (FIF.EDIT, "编辑", lambda: self.edit_dataset(data)),
                    (FIF.SAVE, "取消归档" if data["archived"] else "归档", lambda: self.toggle_archive(data)),
                    (FIF.DELETE, "移入回收站", lambda: self.delete_dataset(data)),
                ],
                compact=True,
            )
        )
        layout.addLayout(row)
        cover_pixmap = self._load_cover(data["cover"])
        if cover_pixmap is not None:
            layout.addWidget(CoverImage(cover_pixmap))
        description = muted(data["description"] or data["tags"] or "尚未添加说明")
        description.setMaximumHeight(40)
        description.setToolTip(data["description"] or data["tags"])
        layout.addWidget(description)
        layout.addStretch()
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        count = BodyLabel(
            f"{data['count']:,} 张图片"
            + (f" · {data['video_count']:,} 段视频" if data["video_count"] else "")
        )
        meta_row.addWidget(count, 1)
        if data["archived"]:
            meta_row.addWidget(StatusPill("已归档", "neutral", dot=False, icon=FIF.SAVE))
        layout.addLayout(meta_row)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        capture = PushButton(FIF.CAMERA, "采集")
        capture.setProperty("softAction", True)
        capture.setEnabled(not data["archived"])
        capture.clicked.connect(lambda: self.acquire.emit(data["id"]))
        browse = PushButton(FIF.PHOTO, "查看素材")
        browse.clicked.connect(lambda: self.browse.emit(data["id"]))
        buttons.addWidget(capture)
        buttons.addWidget(browse)
        layout.addLayout(buttons)
        return card
