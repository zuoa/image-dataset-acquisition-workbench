from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    SegmentedWidget,
    Slider,
    SubtitleLabel,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from ..storage import Cancelled, photo_preview
from ..timeutils import display_now, format_datetime
from .canvas import ImageCanvas
from .common import (
    ComboBox,
    FormDialog,
    confirm,
    divider,
    heading,
    list_view,
    menu_button,
    muted,
    panel,
    video_duration,
)
from .models import ROLE, MediaModel, PhotoDelegate, media_title
from .theme import BACKGROUND, StatusPill


def camera_settings_lines(camera):
    if not isinstance(camera, dict):
        return []
    lines = []
    if camera.get("capture_profile") == "candling":
        lines.append("照蛋模式")
    exposure = camera.get("exposure_time")
    if isinstance(exposure, (int, float)) and exposure > 0:
        lines.append(f"曝光 {exposure * 1000:.3f} ms")
    gain = camera.get("gain_db")
    if isinstance(gain, (int, float)):
        lines.append(f"增益 {gain:.2f} dB")
    return lines


class PhotoDialog(QDialog):
    def __init__(self, app, photo, *, photos=None, index=0):
        super().__init__(app)
        self.setObjectName("photoDialog")
        # Native dialogs can inherit the OS dark palette while Fluent stays light.
        self.setStyleSheet(f"QDialog#photoDialog {{ background: {BACKGROUND}; }}")
        self.setWindowTitle("图片详情 · Vannus")
        self.resize(1120, 760)
        self.setMinimumSize(750, 550)
        self.app = app
        self._interactive = hasattr(app, "refresh")
        self._photos = list(photos) if photos else [photo]
        self._index = max(0, min(index, len(self._photos) - 1))
        self.photo = photo
        self.paths = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.title = SubtitleLabel("")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.installEventFilter(self)
        header.addWidget(self.title, 1)

        self.prev_button = PushButton(FIF.LEFT_ARROW, "上一张")
        self.prev_button.setToolTip("上一张图片 (←)")
        self.prev_button.clicked.connect(lambda: self.step(-1))
        header.addWidget(self.prev_button)

        self.index_pill = StatusPill("1 / 1", "neutral", dot=False)
        header.addWidget(self.index_pill)

        self.next_button = PushButton(FIF.RIGHT_ARROW, "下一张")
        self.next_button.setToolTip("下一张图片 (→)")
        self.next_button.clicked.connect(lambda: self.step(1))
        header.addWidget(self.next_button)

        self.choice = ComboBox()
        self.choice.currentIndexChanged.connect(self.select_image)
        header.addWidget(self.choice)

        self.fit_button = PushButton(FIF.FIT_PAGE, "适应窗口")
        self.fit_button.setToolTip("适应窗口 (0)")
        self.fit_button.clicked.connect(lambda: self.canvas.reset_view())
        header.addWidget(self.fit_button)

        reveal = PushButton(FIF.FOLDER, "打开所在文件夹")
        reveal.clicked.connect(
            lambda: app.open_path(app.workspace.path(self.paths[self.choice.currentIndex()]).parent)
        )
        header.addWidget(reveal)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(14)
        splitter.setChildrenCollapsible(False)
        self.canvas = ImageCanvas()
        splitter.addWidget(self.canvas)

        self.details_panel, box = panel("采集记录", icon=FIF.INFO)
        self.details_panel.setMinimumWidth(272)
        self.details_panel.setMaximumWidth(332)
        self.details_scroll = ScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.details_scroll.enableTransparentBackground()
        box.addWidget(self.details_scroll, 1)

        if self._interactive:
            box.addWidget(divider())
            actions = QHBoxLayout()
            actions.setSpacing(8)
            self.edit_button = PushButton(FIF.EDIT, "编辑信息")
            self.edit_button.clicked.connect(self.edit_photo)
            self.trash_button = PushButton(FIF.DELETE, "移入回收站")
            self.trash_button.setProperty("dangerAction", True)
            self.trash_button.clicked.connect(self.trash_photo)
            actions.addWidget(self.edit_button, 1)
            actions.addWidget(self.trash_button, 1)
            box.addLayout(actions)

        splitter.addWidget(self.details_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([760, 296])
        layout.addWidget(splitter, 1)

        self._load_photo(photo)

    def _elide_title(self):
        self.title.setText(
            self.title.fontMetrics().elidedText(
                self.title.toolTip(), Qt.TextElideMode.ElideRight, max(40, self.title.width())
            )
        )

    def _load_photo(self, photo):
        fresh = self.app.workspace.photo(photo["id"]) if photo and photo.get("id") else None
        self.photo = fresh or photo
        self._photos[self._index] = self.photo
        title_text = media_title(self.photo)
        self.title.setToolTip(title_text)
        self.title.setText(title_text)
        if self.title.width() > 40:
            self._elide_title()

        multi = len(self._photos) > 1
        self.prev_button.setVisible(multi)
        self.next_button.setVisible(multi)
        self.index_pill.setVisible(multi)
        if multi:
            self.prev_button.setEnabled(self._index > 0)
            self.next_button.setEnabled(self._index < len(self._photos) - 1)
            self.index_pill.set_status(f"{self._index + 1} / {len(self._photos)}", "info", dot=False)

        self.paths = [self.photo["original"]]
        with QSignalBlocker(self.choice):
            self.choice.clear()
            self.choice.addItem("原图")
            history = json.loads(self.photo["processing"])
            for processing in reversed(history):
                for crop in processing["crops"]:
                    self.choice.addItem(f"处理 v{processing['version']} · {crop['region']['name']}")
                    self.paths.append(crop["path"])
            default_index = self.paths.index(photo_preview(self.photo))
            self.choice.setCurrentIndex(default_index)
        self.select_image(default_index)

        details = QWidget()
        dl = QVBoxLayout(details)
        dl.setContentsMargins(0, 0, 8, 0)
        dl.setSpacing(14)

        def field(label, value):
            group = QVBoxLayout()
            group.setSpacing(4)
            if label:
                lbl = BodyLabel(label)
                lbl.setObjectName("sectionTitle")
                group.addWidget(lbl)
            text = muted(value or "—")
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            group.addWidget(text)
            dl.addLayout(group)

        created = format_datetime(self.photo["created_at"])
        field(None, f"{self.photo['width']} × {self.photo['height']}\n{created}")
        field("标签", self.photo["tags"])
        field("备注", self.photo["note"])
        metadata = json.loads(self.photo["metadata"])
        field("自定义信息", "\n".join(f"{k}：{v}" for k, v in metadata.get("custom", {}).items()))
        camera = metadata.get("camera", {})
        lines = [camera.get("device", "外部导入图像"), *camera_settings_lines(camera)]
        for processing in history:
            template = processing.get("template") or {}
            lines.append(f"\n处理 v{processing['version']} · {template.get('name', '手动热区')}")
            lines.append(format_datetime(processing["created_at"]))
            lines.extend(
                f"{c['region']['name']}：{c['region']['w']} × {c['region']['h']}" for c in processing["crops"]
            )
        field("相机与处理记录", "\n".join(lines))
        dl.addStretch()
        old = self.details_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.details_scroll.setWidget(details)

    def step(self, delta):
        target = self._index + delta
        if 0 <= target < len(self._photos):
            self._index = target
            self._load_photo(self._photos[target])

    def edit_photo(self):
        dialog = FormDialog(
            self,
            "编辑样本信息",
            [
                ("sample_id", "样本编号", False),
                ("tags", "标签", False),
                ("note", "备注", True),
                ("custom", "自定义信息", True),
            ],
            dict(self.photo, custom=json.loads(self.photo["metadata"]).get("custom", {})),
        )
        if dialog.exec():
            self.app.workspace.update_photos([self.photo["id"]], **dialog.values())
            self._load_photo(self.photo)
            self.app.refresh()

    def trash_photo(self):
        if not confirm(self, "移入回收站？", "将当前图片及裁剪结果移入回收站。", True):
            return
        self.app.workspace.trash("photo", [self.photo["id"]])
        self.app.refresh()
        self._photos.pop(self._index)
        if not self._photos:
            self.accept()
            return
        self._index = min(self._index, len(self._photos) - 1)
        self._load_photo(self._photos[self._index])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            self.step(-1)
        elif event.key() == Qt.Key.Key_Right:
            self.step(1)
        elif event.key() == Qt.Key.Key_0:
            self.canvas.reset_view()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, widget, event):
        if widget is self.title and event.type() == QEvent.Type.Resize:
            self._elide_title()
        return super().eventFilter(widget, event)

    def select_image(self, index):
        if 0 <= index < len(self.paths):
            image = QImage(str(self.app.workspace.path(self.paths[index])))
            self.canvas.set_image(image)


class ChoiceDialog(FormDialog):
    def __init__(self, parent, title, label, choices):
        super().__init__(parent, title, [])
        self.viewLayout.insertWidget(1, BodyLabel(label))
        self.combo = ComboBox()
        self.combo.addItems(choices)
        self.viewLayout.insertWidget(2, self.combo)

    def validate(self):
        return self.combo.currentIndex() >= 0


class ImportImagesDialog(FormDialog):
    def __init__(self, parent, count, dataset_id=None, batch_id=None):
        super().__init__(parent, "导入图片", [])
        self.workspace = parent.app.workspace
        self.datasets = self.workspace.datasets()
        self.batches = []
        self.preferred_batch = batch_id
        self.viewLayout.insertWidget(1, muted(f"已选择 {count} 张图片，原图将保留，不自动套用裁剪模板。"))
        form = QFormLayout()
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.dataset_combo = ComboBox()
        self.dataset_combo.setAccessibleName("导入到数据集")
        for dataset in self.datasets:
            self.dataset_combo.addItem(
                dataset["name"] + ("（已归档）" if dataset["archived"] else ""), userData=dataset["id"]
            )
        self.dataset_combo.addItem("＋ 新建数据集", userData=None)
        form.addRow(BodyLabel("数据集"), self.dataset_combo)
        self.dataset_name = LineEdit()
        self.dataset_name.setPlaceholderText("填写新数据集名称")
        self.dataset_name_label = BodyLabel("数据集名称")
        form.addRow(self.dataset_name_label, self.dataset_name)
        self.batch_combo = ComboBox()
        self.batch_combo.setAccessibleName("导入到批次")
        form.addRow(BodyLabel("批次"), self.batch_combo)
        self.batch_name = LineEdit()
        self.batch_name.setText("图片导入 · " + display_now().strftime("%Y-%m-%d"))
        self.batch_name_label = BodyLabel("批次名称")
        form.addRow(self.batch_name_label, self.batch_name)
        self.viewLayout.insertLayout(2, form)
        self.guidance = muted("")
        self.viewLayout.insertWidget(3, self.guidance)
        self.widget.setMinimumWidth(500)
        self.dataset_combo.setCurrentIndex(
            next((i for i, dataset in enumerate(self.datasets) if dataset["id"] == dataset_id), 0)
        )
        self.dataset_combo.currentIndexChanged.connect(self.dataset_changed)
        self.batch_combo.currentIndexChanged.connect(self.batch_changed)
        self.dataset_changed()

    def selected_dataset(self):
        dataset_id = self.dataset_combo.currentData()
        return next((dataset for dataset in self.datasets if dataset["id"] == dataset_id), None)

    def dataset_changed(self, *_):
        dataset = self.selected_dataset()
        self.dataset_name.setVisible(dataset is None)
        self.dataset_name_label.setVisible(dataset is None)
        self.batches = self.workspace.batches(dataset["id"]) if dataset else []
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        for batch in self.batches:
            self.batch_combo.addItem(batch["name"], userData=batch["id"])
        self.batch_combo.addItem("＋ 新建批次", userData=None)
        self.batch_combo.setCurrentIndex(
            next((i for i, batch in enumerate(self.batches) if batch["id"] == self.preferred_batch), 0)
        )
        self.batch_combo.blockSignals(False)
        archived = dataset and dataset["archived"]
        self.yesButton.setText("取消归档并导入" if archived else "导入图片")
        self.guidance.setText(
            "此数据集已归档，导入时会取消归档；也可选择其他数据集。"
            if archived
            else "可在这里新建数据集和批次，确认导入后才会创建。"
        )
        self.batch_changed()

    def batch_changed(self, *_):
        new_batch = self.batch_combo.currentData() is None
        self.batch_name.setVisible(new_batch)
        self.batch_name_label.setVisible(new_batch)
        self.error_label.clear()

    def validate(self):
        for is_new, edit, label in (
            (self.selected_dataset() is None, self.dataset_name, "数据集名称"),
            (self.batch_combo.currentData() is None, self.batch_name, "批次名称"),
        ):
            if is_new and not 0 < len(edit.text().strip()) <= 200:
                self.error_label.setText(f"{label}不能为空，且不能超过 200 个字符")
                edit.setFocus()
                return False
        self.error_label.clear()
        return True

    def destination(self):
        dataset = self.selected_dataset()
        batch = next((batch for batch in self.batches if batch["id"] == self.batch_combo.currentData()), None)
        return dict(
            dataset_id=dataset["id"] if dataset else None,
            dataset_name=self.dataset_name.text().strip(),
            unarchive=bool(dataset and dataset["archived"]),
            batch_id=batch["id"] if batch else None,
            batch_name=self.batch_name.text().strip(),
            defaults=json.loads(batch["defaults"]) if batch else {},
        )


class LibraryPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("library")
        self.datasets = []
        self.batches = []
        self.current_filters = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 16)
        layout.setSpacing(14)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(heading("素材库"), 1)
        import_button = PushButton(FIF.ADD, "导入图片")
        import_button.clicked.connect(self.import_images)
        row.addWidget(import_button)
        export = PushButton(FIF.SHARE, "导出 ZIP")
        export.clicked.connect(self.export)
        row.addWidget(export)
        self.continue_button = PrimaryPushButton(FIF.CAMERA, "继续采集")
        self.continue_button.clicked.connect(self.continue_capture)
        row.addWidget(self.continue_button)
        layout.addLayout(row)

        types = QHBoxLayout()
        types.setSpacing(10)
        self.type_tabs = SegmentedWidget(self)
        for key, label in (("all", "全部"), ("photo", "图片"), ("video", "视频")):
            self.type_tabs.addItem(key, label)
        self.type_tabs.setCurrentItem("all")
        self.type_tabs.setFixedWidth(260)
        self.type_tabs.setAccessibleName("素材类型")
        types.addWidget(self.type_tabs)
        types.addStretch()
        self.selection_count_pill = StatusPill("", "info", dot=False)
        self.selection_count_pill.hide()
        types.addWidget(self.selection_count_pill)
        self.select_all_button = PushButton(FIF.CHECKBOX, "全选")
        self.select_all_button.clicked.connect(lambda: self.view.selectAll())
        self.select_all_button.hide()
        types.addWidget(self.select_all_button)
        self.clear_selection_button = PushButton(FIF.CLEAR_SELECTION, "取消选择")
        self.clear_selection_button.clicked.connect(lambda: self.view.clearSelection())
        self.clear_selection_button.hide()
        types.addWidget(self.clear_selection_button)
        layout.addLayout(types)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.dataset_combo = ComboBox()
        self.dataset_combo.setMinimumWidth(160)
        self.dataset_combo.currentIndexChanged.connect(self.dataset_changed)
        filters.addWidget(self.dataset_combo)
        self.batch_combo = ComboBox()
        self.batch_combo.setMinimumWidth(145)
        self.batch_combo.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.batch_combo)
        self.date_combo = ComboBox()
        self.date_combo.setMinimumWidth(125)
        self.date_combo.addItems(["全部时间", "最近 7 天", "最近 30 天"])
        self.date_combo.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.date_combo)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("搜索样本编号、标签或备注")
        self.search.setClearButtonEnabled(True)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.debounce.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda: self.debounce.start())
        filters.addWidget(self.search, 1)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)
        splitter.setChildrenCollapsible(False)
        self.model = MediaModel(app.workspace, self)
        self.delegate = PhotoDelegate(self)
        self.view = list_view(self, self.model, self.delegate)
        self.view.doubleClicked.connect(lambda index: self._open_item(index.data(ROLE)))
        self.view.selectionModel().selectionChanged.connect(self.selection_changed)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        self.view.installEventFilter(self)
        splitter.addWidget(self.view)

        side, box = panel("素材信息", icon=FIF.INFO)
        side.setMinimumWidth(256)
        side.setMaximumWidth(300)
        self.details_panel = side
        self.selection_badge = StatusPill("未选择", "neutral", dot=False)
        box.addWidget(self.selection_badge)
        self.selection_title = BodyLabel("尚未选择素材")
        self.selection_title.setWordWrap(True)
        box.addWidget(self.selection_title)
        self.selection_details = muted("")
        box.addWidget(self.selection_details)
        box.addWidget(divider())
        self.play_button = PushButton(FIF.VIDEO, "播放视频")
        self.play_button.clicked.connect(self.play_selected)
        box.addWidget(self.play_button)
        self.reveal_button = PushButton(FIF.FOLDER, "打开所在文件夹")
        self.reveal_button.clicked.connect(self.reveal_selected)
        box.addWidget(self.reveal_button)
        self.edit_button = PushButton(FIF.EDIT, "编辑信息")
        self.edit_button.clicked.connect(self.edit_selected)
        box.addWidget(self.edit_button)
        self.photo_actions = menu_button(
            "更多图片操作",
            [
                (FIF.MOVE, "移动到其他批次", self.move_selected),
                (FIF.CLIPPING_TOOL, "重新裁剪", self.reprocess_selected),
            ],
            icon=FIF.MORE,
        )
        box.addWidget(self.photo_actions)
        self.delete_button = PushButton(FIF.DELETE, "移入回收站")
        self.delete_button.setProperty("dangerAction", True)
        self.delete_button.clicked.connect(self.delete_selected)
        box.addWidget(self.delete_button)
        box.addStretch()
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([860, 272])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.count_label = muted("")
        self.count_label.setWordWrap(False)
        self.model.total_changed.connect(self.update_summary)
        footer.addWidget(self.count_label)
        self.empty_label = muted("暂无匹配素材，可调整筛选、继续采集或导入图片。")
        self.empty_label.setWordWrap(False)
        footer.addWidget(self.empty_label)
        footer.addStretch(1)
        self.model.total_changed.connect(lambda total: self.empty_label.setVisible(total == 0))
        thumb_label = muted("缩略图")
        thumb_label.setWordWrap(False)
        footer.addWidget(thumb_label)
        slider = Slider(Qt.Orientation.Horizontal)
        slider.setRange(170, 320)
        slider.setValue(220)
        slider.setFixedWidth(124)
        slider.valueChanged.connect(self.resize_cards)
        footer.addWidget(slider)
        layout.addLayout(footer)

        self.type_tabs.currentItemChanged.connect(self.reload)
        self.refresh()

    def _open_item(self, item):
        if not item:
            return
        if item.get("media_type") == "video":
            self.app.show_media(item)
            return
        photos = [row for row in self.model.rows if row.get("media_type") == "photo"]
        index = next((i for i, row in enumerate(photos) if row["id"] == item["id"]), 0)
        self.app.show_photo(item, photos=photos, index=index)

    def _show_context_menu(self, pos):
        index = self.view.indexAt(pos)
        if not index.isValid():
            return
        if not self.view.selectionModel().isSelected(index):
            self.view.setCurrentIndex(index)
        items = self.selected()
        if not items:
            return
        only_photos = all(item["media_type"] == "photo" for item in items)
        menu = RoundMenu(parent=self.view)
        if len(items) == 1:
            label = "播放视频" if items[0]["media_type"] == "video" else "查看大图"
            act = Action(FIF.VIDEO if items[0]["media_type"] == "video" else FIF.PHOTO, label, menu)
            act.triggered.connect(lambda: self._open_item(items[0]))
            menu.addAction(act)
            reveal = Action(FIF.FOLDER, "打开所在文件夹", menu)
            reveal.triggered.connect(self.reveal_selected)
            menu.addAction(reveal)
        if only_photos:
            edit = Action(FIF.EDIT, "编辑信息", menu)
            edit.triggered.connect(self.edit_selected)
            menu.addAction(edit)
            move = Action("移动到其他批次", menu)
            move.triggered.connect(self.move_selected)
            menu.addAction(move)
            reprocess = Action("重新裁剪", menu)
            reprocess.triggered.connect(self.reprocess_selected)
            menu.addAction(reprocess)
        menu.addSeparator()
        trash = Action(FIF.DELETE, "移入回收站", menu)
        trash.triggered.connect(self.delete_selected)
        menu.addAction(trash)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def eventFilter(self, widget, event):
        if widget is self.view and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                items = self.selected()
                if len(items) == 1:
                    self._open_item(items[0])
                    return True
            elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self.selected():
                    self.delete_selected()
                    return True
            elif key == Qt.Key.Key_Escape:
                if self.selected():
                    self.view.clearSelection()
                    return True
        return super().eventFilter(widget, event)

    def dataset_id(self):
        i = self.dataset_combo.currentIndex()
        return self.datasets[i]["id"] if 0 <= i < len(self.datasets) else None

    def selected(self):
        return [i.data(ROLE) for i in self.view.selectionModel().selectedIndexes()]

    def select_dataset(self, dataset_id, media_type="all", batch_id=None):
        self.dataset_combo.setCurrentIndex(
            next((i for i, d in enumerate(self.datasets) if d["id"] == dataset_id), -1)
        )
        self.batch_combo.setCurrentIndex(max(0, self.batch_combo.findData(batch_id or "")))
        self.type_tabs.setCurrentItem(media_type)

    def continue_capture(self):
        if not self.continue_button.isEnabled():
            return
        self.app.open_capture(self.dataset_id(), self.batch_combo.currentData() or None)
        media_type = self.type_tabs.currentRouteKey()
        if media_type != "all":
            self.app.capture.mode_combo.setCurrentIndex(1 if media_type == "video" else 0)

    def refresh(self):
        previous = self.dataset_id()
        self.datasets = self.app.workspace.datasets()
        self.dataset_combo.blockSignals(True)
        self.dataset_combo.clear()
        self.dataset_combo.addItems([d["name"] for d in self.datasets])
        self.dataset_combo.setCurrentIndex(
            next((i for i, d in enumerate(self.datasets) if d["id"] == previous), 0 if self.datasets else -1)
        )
        self.dataset_combo.blockSignals(False)
        self.dataset_changed(preserve=True)

    def dataset_changed(self, *_, preserve=False):
        dataset = next((d for d in self.datasets if d["id"] == self.dataset_id()), None)
        self.continue_button.setEnabled(dataset is not None and not dataset["archived"])
        self.continue_button.setToolTip(
            "请先在数据集工作台取消归档" if dataset and dataset["archived"] else "继续采集当前数据集的素材"
        )
        previous = self.batch_combo.currentData() if preserve else None
        self.batches = self.app.workspace.batches(self.dataset_id()) if self.dataset_id() else []
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        self.batch_combo.addItem("全部批次", userData="")
        for batch in self.batches:
            self.batch_combo.addItem(batch["name"], userData=batch["id"])
        self.batch_combo.setCurrentIndex(
            next((i + 1 for i, b in enumerate(self.batches) if b["id"] == previous), 0)
        )
        self.batch_combo.blockSignals(False)
        self.reload()

    def reload(self, *_):
        self.current_filters = dict(
            dataset_id=self.dataset_id() or "__none__",
            batch_id=self.batch_combo.currentData() or None,
            search=self.search.text(),
            media_type=self.type_tabs.currentRouteKey(),
        )
        days = {1: 7, 2: 30}.get(self.date_combo.currentIndex())
        if days:
            self.current_filters["date_from"] = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()
        self.model.reload(**self.current_filters)
        self.selection_changed()

    def resize_cards(self, value):
        self.delegate.card_width = value
        self.view.doItemsLayout()

    def update_summary(self, total):
        self.count_label.setText(
            f"共 {total:,} 项素材 · {self.model.counts['photo']:,} 张图片 · {self.model.counts['video']:,} 段视频"
        )
        self.select_all_button.setVisible(total > 0)

    def selection_changed(self, *_):
        items = self.selected()
        only_photos = bool(items) and all(item["media_type"] == "photo" for item in items)
        self.edit_button.setVisible(only_photos)
        self.photo_actions.setVisible(only_photos)
        self.play_button.setVisible(len(items) == 1 and items[0]["media_type"] == "video")
        self.reveal_button.setVisible(len(items) == 1)
        self.delete_button.setVisible(bool(items))
        self.selection_count_pill.setVisible(bool(items))
        self.clear_selection_button.setVisible(bool(items))
        if items:
            self.selection_count_pill.set_status(f"已选 {len(items)} 项", "info", dot=False)

        if not items:
            self.selection_badge.set_status("未选择", "neutral", dot=False)
            self.selection_title.setText("尚未选择素材")
            self.selection_details.setText(
                "单击选中查看详情，双击打开大图或播放视频。\n按 Ctrl / ⌘ 或 Shift 可多选，右键打开快捷菜单。"
            )
        elif len(items) > 1:
            self.selection_badge.set_status(f"已选 {len(items)} 项", "info", dot=False)
            self.selection_title.setText(f"已选择 {len(items)} 项素材")
            photos = sum(item["media_type"] == "photo" for item in items)
            self.selection_details.setText(f"{photos} 张图片 · {len(items) - photos} 段视频")
        else:
            item = items[0]
            is_video = item["media_type"] == "video"
            self.selection_badge.set_status(
                "视频" if is_video else "图片", "candling" if is_video else "info", dot=False
            )
            self.selection_title.setText(media_title(item))
            dimensions = (
                f"视频 · {video_duration(item['duration_ms'])}"
                if is_video
                else f"图片 · {item['width']} × {item['height']}"
            )
            metadata = json.loads(item["metadata"])
            details = camera_settings_lines(metadata.get("camera"))
            details.extend(f"{key}：{value}" for key, value in metadata.get("custom", {}).items())
            self.selection_details.setText(
                f"{dimensions}\n{item['batch_name']}\n{format_datetime(item['created_at'])}"
                f"\n\n{item['tags']}\n{item['note']}\n"
                + "\n".join(details)
            )

    def require_selection(self):
        items = self.selected()
        if not items:
            self.app.notify("请先选择素材", error=True)
        return items

    def require_photos(self):
        items = self.require_selection()
        if any(item["media_type"] != "photo" for item in items):
            self.app.notify("此操作仅适用于图片，请只选择图片", error=True)
            return []
        return items

    def play_selected(self):
        items = self.selected()
        if len(items) == 1 and items[0]["media_type"] == "video":
            self.app.show_media(items[0])

    def reveal_selected(self):
        items = self.selected()
        if len(items) == 1:
            self.app.open_path(self.app.workspace.path(items[0]["original"]).parent)

    def edit_selected(self):
        photos = self.require_photos()
        if not photos:
            return
        if len(photos) == 1:
            dialog = FormDialog(
                self,
                "编辑样本信息",
                [
                    ("sample_id", "样本编号", False),
                    ("tags", "标签", False),
                    ("note", "备注", True),
                    ("custom", "自定义信息", True),
                ],
                dict(photos[0], custom=json.loads(photos[0]["metadata"]).get("custom", {})),
            )
            if dialog.exec():
                self.app.workspace.update_photos([photos[0]["id"]], **dialog.values())
                self.reload()
        else:
            choice = ChoiceDialog(self, "批量编辑", "选择需要统一修改的字段", ["样本编号", "标签", "备注"])
            if choice.exec():
                key = ["sample_id", "tags", "note"][choice.combo.currentIndex()]
                dialog = FormDialog(
                    self, f"修改 {len(photos)} 张图片", [(key, "新值（留空即清空）", key == "note")]
                )
                if dialog.exec():
                    self.app.workspace.update_photos([p["id"] for p in photos], **dialog.values())
                    self.reload()

    def move_selected(self):
        photos = self.require_photos()
        if not photos:
            return
        destinations = [
            (d, b) for d in self.app.workspace.datasets(False) for b in self.app.workspace.batches(d["id"])
        ]
        dialog = ChoiceDialog(
            self, "移动图片", "选择目标数据集与批次", [f"{d['name']} / {b['name']}" for d, b in destinations]
        )
        if dialog.exec():
            dataset, batch = destinations[dialog.combo.currentIndex()]
            self.app.workspace.move_photos([p["id"] for p in photos], dataset["id"], batch["id"])
            self.app.refresh()

    def delete_selected(self):
        items = self.require_selection()
        if items and confirm(
            self, "移入回收站？", f"将 {len(items)} 项素材及图片的裁剪结果移入回收站。", True
        ):
            for kind in ("photo", "video"):
                ids = [item["id"] for item in items if item["media_type"] == kind]
                if ids:
                    self.app.workspace.trash(kind, ids)
            self.app.refresh()

    def reprocess_selected(self):
        photos = self.require_photos()
        templates = self.app.workspace.templates(self.dataset_id()) if self.dataset_id() else []
        if not photos:
            return
        if not templates:
            self.app.notify("请先在采集工作台保存裁剪模板", error=True)
            return
        dialog = ChoiceDialog(
            self,
            "重新裁剪",
            "选择模板；新建处理版本，保留历史裁剪和原图",
            [f"{t['name']} v{t['version']} · {t['width']}×{t['height']}" for t in templates],
        )
        if dialog.exec():
            template = templates[dialog.combo.currentIndex()]
            if any((p["width"], p["height"]) != (template["width"], template["height"]) for p in photos):
                self.app.notify("所选图片尺寸与模板不一致，请分别选择匹配尺寸的图片", error=True)
                return

            def run(cancel, progress):
                for i, photo in enumerate(photos):
                    if cancel.is_set():
                        raise Cancelled("已停止，已完成的处理版本保留")
                    self.app.workspace.reprocess(photo["id"], template)
                    progress(i + 1, len(photos))
                return len(photos)

            self.app.start_task("正在生成新的裁剪版本…", run, lambda _: self.app.refresh())

    def import_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要归档的图片", "", "图像 (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)"
        )
        if not paths:
            return
        dialog = ImportImagesDialog(self, len(paths), self.dataset_id(), self.batch_combo.currentData())
        if dialog.exec():
            destination = dialog.destination()

            def run(cancel, progress):
                if cancel.is_set():
                    raise Cancelled("已取消导入")
                workspace = self.app.workspace
                dataset_id = destination["dataset_id"] or workspace.save_dataset(destination["dataset_name"])
                if destination["unarchive"]:
                    workspace.archive_dataset(dataset_id, False)
                batch_id = destination["batch_id"] or workspace.save_batch(
                    dataset_id, destination["batch_name"]
                )
                failures = []
                count = 0
                for index, path in enumerate(paths):
                    if cancel.is_set():
                        raise Cancelled(f"已停止导入，已保存 {count} 张图片")
                    try:
                        workspace.add_photo(path, dataset_id, batch_id, **destination["defaults"])
                        count += 1
                    except Exception as error:
                        failures.append(f"{path}: {error}")
                    progress(index + 1, len(paths))
                return dict(count=count, failures=failures, dataset_id=dataset_id, batch_id=batch_id)

            def done(result):
                self.app.refresh()
                self.search.clear()
                self.date_combo.setCurrentIndex(0)
                self.select_dataset(result["dataset_id"], "photo", result["batch_id"])
                self.reload()
                self.app.notify(
                    f"已导入 {result['count']} 张图片"
                    + (f"；{len(result['failures'])} 张失败，请查看日志" if result["failures"] else "")
                )
                if result["failures"]:
                    import logging

                    for error in result["failures"]:
                        logging.error("Import: %s", error)

            self.app.start_task("正在导入图片…", run, done)

    def export(self):
        dataset_id = self.dataset_id()
        if not dataset_id:
            self.app.notify("请先选择数据集", error=True)
            return
        choices = [
            "整个数据集 · 完整归档，可恢复",
            "当前筛选结果 · 完整归档，可恢复",
        ]
        if self.type_tabs.currentRouteKey() != "video":
            choices.append("当前筛选结果 · 仅图片裁剪图，不能完整恢复")
        dialog = ChoiceDialog(
            self,
            "导出数据集",
            "选择导出内容",
            choices,
        )
        if not dialog.exec():
            return
        choice = dialog.combo.currentIndex()
        destination, _ = QFileDialog.getSaveFileName(self, "导出 ZIP", "数据集.zip", "ZIP 归档 (*.zip)")
        if not destination:
            return
        filters = {k: v for k, v in self.current_filters.items() if k != "dataset_id"} if choice else {}
        self.app.start_task(
            "正在导出数据集…",
            lambda cancel, progress: self.app.workspace.export_zip(
                destination,
                dataset_id,
                filters=filters,
                crops_only=choice == 2,
                cancel=cancel,
                progress=progress,
            ),
            lambda count: self.app.notify(
                f"已导出 {count} 条图片 / 视频记录的归档"
                if choice != 2
                else f"已导出 {count} 张图片的裁剪结果"
            ),
        )
