import shutil
from datetime import datetime, timezone

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListView,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, LineEdit, PrimaryPushButton, PushButton, ScrollArea, SubtitleLabel
from qfluentwidgets import FluentIcon as FIF

from .common import FormDialog, confirm, heading, menu_button, muted
from .models import ROLE, MediaModel
from .theme import INK, TONES, MetricCard, SoftCard
from .typography import interface_font


class ColorIcon(QWidget):
    def __init__(self, icon, tone, parent=None):
        super().__init__(parent)
        self.icon, self.tone = icon, tone
        self.setFixedSize(44, 44)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.tone[0]))
        painter.drawRoundedRect(QRectF(self.rect()), 13, 13)
        self.icon.render(painter, QRectF(12, 12, 20, 20), fill=self.tone[1])


class RecentDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(240, 76)

    def paint(self, painter, option, index):
        row = index.data(ROLE)
        if not row:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        thumb = QRectF(rect.x(), rect.y() + 12, 48, 48)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F1F4FB"))
        painter.drawRoundedRect(thumb, 13, 13)
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if pixmap and not pixmap.isNull():
            painter.drawPixmap(thumb.toRect().adjusted(3, 3, -3, -3), pixmap, pixmap.rect())
        else:
            icon = FIF.VIDEO if row.get("media_type") == "video" else FIF.PHOTO
            icon.render(painter, thumb.adjusted(15, 15, -15, -15), fill="#AAB9DB")
        painter.setFont(interface_font(16))
        painter.setPen(QColor(INK))
        width = max(20, round(rect.width() - 62))
        title = row["sample_id"] or row["id"][:8].upper()
        painter.drawText(
            QRectF(rect.x() + 62, rect.y() + 13, width, 24),
            painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, width),
        )
        painter.setFont(interface_font(13))
        painter.setPen(QColor("#9CA1B4"))
        date = datetime.fromisoformat(row["created_at"]).astimezone().strftime("%m/%d %H:%M")
        text = f"{row['dataset_name']} · {date}"
        painter.drawText(
            QRectF(rect.x() + 62, rect.y() + 40, width, 22),
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 26)
        layout.setSpacing(24)
        header = QHBoxLayout()
        header.addWidget(heading("数据集工作台", "连接相机，采集并整理实验图像。"), 1)
        import_button = PushButton("导入归档")
        import_button.clicked.connect(app.import_archive)
        header.addWidget(import_button)
        create = PrimaryPushButton(FIF.ADD, "新建数据集")
        create.clicked.connect(self.create_dataset)
        header.addWidget(create)
        layout.addLayout(header)
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.metrics = []
        for title, icon, tone in zip(
            ["数据集", "原始图片", "今日新增", "已归档"], [FIF.FOLDER, FIF.PHOTO, FIF.CAMERA, FIF.SAVE], TONES
        ):
            card = MetricCard(title, icon, tone)
            self.metrics.append(card)
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        body = QHBoxLayout()
        body.setSpacing(24)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(18)
        row = QHBoxLayout()
        row.addWidget(SubtitleLabel("我的数据集"), 1)
        self.search = LineEdit()
        self.search.setPlaceholderText("搜索数据集")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(210)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search)
        ll.addLayout(row)
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(2, 2, 2, 10)
        self.grid.setSpacing(18)
        scroll.setWidget(self.content)
        ll.addWidget(scroll, 1)
        self.summary = muted("")
        ll.addWidget(self.summary)
        body.addWidget(left, 1)

        aside = QWidget()
        aside.setFixedWidth(284)
        al = QVBoxLayout(aside)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(22)
        recent = SoftCard()
        box = QVBoxLayout(recent)
        box.setContentsMargins(22, 22, 22, 18)
        box.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("最近采集"), 1)
        self.all_recent = PushButton("全部")
        self.all_recent.clicked.connect(self.browse_recent)
        top.addWidget(self.all_recent)
        box.addLayout(top)
        self.recent_model = MediaModel(app.workspace, self)
        self.recent_view = QListView()
        self.recent_view.setObjectName("gallery")
        self.recent_view.setModel(self.recent_model)
        self.recent_view.setItemDelegate(RecentDelegate(self))
        self.recent_view.setUniformItemSizes(True)
        self.recent_view.setFixedHeight(305)
        self.recent_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_view.doubleClicked.connect(lambda i: app.show_media(i.data(ROLE)))
        box.addWidget(self.recent_view)
        self.no_recent = muted("还没有采集记录。\n新建一个数据集，开始记录第一张图像。")
        self.no_recent.setMinimumHeight(120)
        box.addWidget(self.no_recent)
        al.addWidget(recent)
        workspace_card = SoftCard(tint="#EFF4FF", shadow=False)
        box = QVBoxLayout(workspace_card)
        box.setContentsMargins(22, 22, 22, 22)
        box.setSpacing(12)
        box.addWidget(ColorIcon(FIF.FOLDER, TONES[3]))
        box.addWidget(SubtitleLabel("本地工作区"))
        box.addWidget(muted("图像保存在本机，离线也能继续采集。"))
        self.free_space = muted("")
        box.addWidget(self.free_space)
        open_workspace = PushButton("打开文件夹")
        open_workspace.clicked.connect(lambda: app.open_path(app.workspace.root))
        box.addWidget(open_workspace)
        al.addWidget(workspace_card)
        al.addStretch()
        body.addWidget(aside)
        layout.addLayout(body, 1)
        self.refresh()

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

    def refresh(self):
        datasets = self.app.workspace.datasets()
        total = sum(data["count"] for data in datasets)
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        today = self.app.workspace.count_photos(date_from=midnight.astimezone(timezone.utc).isoformat())
        values = [len(datasets), total, today, sum(bool(d["archived"]) for d in datasets)]
        for card, value in zip(self.metrics, values):
            card.set_value(value)
        video_total = sum(data["video_count"] for data in datasets)
        self.summary.setText(
            f"{len(datasets)} 个数据集 · {total:,} 张原图 · {video_total:,} 段视频 · 本地保存"
        )
        self.free_space.setText(
            f"磁盘可用 {shutil.disk_usage(self.app.workspace.root).free / 1024**3:.1f} GB"
        )
        self.recent_model.reload()
        self.recent_view.setVisible(total + video_total > 0)
        self.all_recent.setVisible(total + video_total > 0)
        self.no_recent.setVisible(total + video_total == 0)
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        search = self.search.text().strip().casefold()
        rows = [d for d in datasets if search in (d["name"] + d["tags"] + d["description"]).casefold()]
        if not rows:
            card = SoftCard()
            box = QVBoxLayout(card)
            box.setContentsMargins(28, 34, 28, 34)
            box.setSpacing(16)
            box.addWidget(ColorIcon(FIF.ADD, TONES[0]))
            box.addWidget(SubtitleLabel("建立第一个数据集" if not datasets else "没有匹配的数据集"))
            box.addWidget(
                muted(
                    "点击右上角「新建数据集」，开始采集实验图像。" if not datasets else "试试其他名称或标签。"
                )
            )
            self.grid.addWidget(card, 0, 0, 1, 2)
        else:
            for index, data in enumerate(rows):
                self.grid.addWidget(self._card(data, TONES[index % len(TONES)]), index // 2, index % 2)
        for column in range(2):
            self.grid.setColumnStretch(column, 1)
        self.grid.setRowStretch((len(rows) + 1) // 2 + 1, 1)

    def _card(self, data, tone):
        card = SoftCard()
        card.setMinimumWidth(245)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(15)
        row = QHBoxLayout()
        row.addWidget(ColorIcon(FIF.FOLDER, tone))
        title = SubtitleLabel(data["name"])
        title.setWordWrap(True)
        title.setMaximumHeight(60)
        title.setToolTip(data["name"])
        row.addWidget(title, 1)
        row.addWidget(
            menu_button(
                "数据集操作",
                [
                    ("编辑", lambda: self.edit_dataset(data)),
                    ("取消归档" if data["archived"] else "归档", lambda: self.toggle_archive(data)),
                    ("移入回收站", lambda: self.delete_dataset(data)),
                ],
                compact=True,
            )
        )
        layout.addLayout(row)
        if data["cover"]:
            cover = QLabel()
            cover.setFixedHeight(105)
            cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image = QPixmap(str(self.app.workspace.path(data["cover"])))
            cover.setPixmap(
                image.scaled(
                    320, 105, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
            layout.addWidget(cover)
        description = muted(data["description"] or data["tags"] or "尚未添加说明")
        description.setMaximumHeight(46)
        description.setToolTip(data["description"] or data["tags"])
        layout.addWidget(description)
        layout.addStretch()
        count = BodyLabel(
            f"{data['count']:,} 张图片"
            + (f" · {data['video_count']:,} 段视频" if data["video_count"] else "")
        )
        layout.addWidget(count)
        if data["archived"]:
            layout.addWidget(muted("已归档"))
        buttons = QHBoxLayout()
        capture = PushButton("采集")
        capture.setProperty("softAction", True)
        capture.setEnabled(not data["archived"])
        capture.clicked.connect(lambda: self.acquire.emit(data["id"]))
        browse = PushButton("查看素材")
        browse.clicked.connect(lambda: self.browse.emit(data["id"]))
        buttons.addWidget(capture)
        buttons.addWidget(browse)
        layout.addLayout(buttons)
        return card
