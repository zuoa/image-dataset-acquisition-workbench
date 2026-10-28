from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QEvent, Qt, QTimer
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
    BodyLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    Slider,
    SubtitleLabel,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from ..storage import Cancelled
from .canvas import ImageCanvas
from .common import (
    ComboBox,
    FormDialog,
    confirm,
    heading,
    list_view,
    menu_button,
    muted,
    panel,
    video_duration,
)
from .models import ROLE, MediaModel, PhotoDelegate
from .theme import BACKGROUND


class PhotoDialog(QDialog):
    def __init__(self, app, photo):
        super().__init__(app)
        self.setObjectName("photoDialog")
        # Native dialogs can inherit the OS dark palette while Fluent stays light.
        self.setStyleSheet(f"QDialog#photoDialog {{ background: {BACKGROUND}; }}")
        self.setWindowTitle("图片详情 · Vannus")
        self.resize(1100, 760)
        self.setMinimumSize(750, 550)
        self.app, self.photo = app, photo
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.title = SubtitleLabel(photo["sample_id"] or photo["id"][:12].upper())
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setToolTip(self.title.text())
        self.title.installEventFilter(self)
        header.addWidget(self.title, 1)
        self.choice = ComboBox()
        self.paths = [photo["original"]]
        self.choice.addItem("原图")
        history = json.loads(photo["processing"])
        for processing in reversed(history):
            for crop in processing["crops"]:
                self.choice.addItem(f"处理 v{processing['version']} · {crop['region']['name']}")
                self.paths.append(crop["path"])
        self.choice.currentIndexChanged.connect(self.select_image)
        header.addWidget(self.choice)
        reveal = PushButton(FIF.FOLDER, "打开所在文件夹")
        reveal.clicked.connect(
            lambda: app.open_path(app.workspace.path(self.paths[self.choice.currentIndex()]).parent)
        )
        header.addWidget(reveal)
        layout.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(16)
        splitter.setChildrenCollapsible(False)
        self.canvas = ImageCanvas()
        splitter.addWidget(self.canvas)
        self.details_panel, box = panel("采集记录")
        self.details_panel.setMinimumWidth(280)
        self.details_panel.setMaximumWidth(340)
        details = QWidget()
        dl = QVBoxLayout(details)
        dl.setContentsMargins(0, 0, 10, 0)
        dl.setSpacing(18)

        def field(label, value):
            group = QVBoxLayout()
            group.setSpacing(6)
            if label:
                group.addWidget(BodyLabel(label))
            text = muted(value or "—")
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            group.addWidget(text)
            dl.addLayout(group)

        created = photo["created_at"][:19].replace("T", " ") + " UTC"
        field(None, f"{photo['width']} × {photo['height']}\n{created}")
        field("标签", photo["tags"])
        field("备注", photo["note"])
        metadata = json.loads(photo["metadata"])
        field("自定义信息", "\n".join(f"{k}：{v}" for k, v in metadata.get("custom", {}).items()))
        camera = metadata.get("camera", {})
        lines = [camera.get("device", "外部导入图像")]
        if camera.get("exposure_time"):
            lines.append(f"曝光 {camera['exposure_time'] * 1000:.2f} ms")
        for processing in history:
            template = processing.get("template") or {}
            lines.append(f"\n处理 v{processing['version']} · {template.get('name', '手动热区')}")
            lines.append(processing["created_at"][:19].replace("T", " ") + " UTC")
            lines.extend(
                f"{c['region']['name']}：{c['region']['w']} × {c['region']['h']}" for c in processing["crops"]
            )
        field("相机与处理记录", "\n".join(lines))
        dl.addStretch()
        self.details_scroll = ScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.details_scroll.setWidget(details)
        self.details_scroll.enableTransparentBackground()
        box.addWidget(self.details_scroll, 1)
        splitter.addWidget(self.details_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([720, 300])
        layout.addWidget(splitter, 1)
        self.select_image(0)

    def eventFilter(self, widget, event):
        if widget is self.title and event.type() == QEvent.Type.Resize:
            self.title.setText(
                self.title.fontMetrics().elidedText(
                    self.title.toolTip(), Qt.TextElideMode.ElideRight, self.title.width()
                )
            )
        return super().eventFilter(widget, event)

    def select_image(self, index):
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
        form.setSpacing(12)
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
        self.batch_name.setText("图片导入 · " + datetime.now().strftime("%Y-%m-%d"))
        self.batch_name_label = BodyLabel("批次名称")
        form.addRow(self.batch_name_label, self.batch_name)
        self.viewLayout.insertLayout(2, form)
        self.guidance = muted("")
        self.viewLayout.insertWidget(3, self.guidance)
        self.widget.setMinimumWidth(520)
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
        layout.setContentsMargins(30, 26, 30, 18)
        layout.setSpacing(18)
        row = QHBoxLayout()
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
        self.type_tabs = SegmentedWidget(self)
        for key, label in (("all", "全部"), ("photo", "图片"), ("video", "视频")):
            self.type_tabs.addItem(key, label)
        self.type_tabs.setCurrentItem("all")
        self.type_tabs.setFixedWidth(300)
        self.type_tabs.setAccessibleName("素材类型")
        types.addWidget(self.type_tabs)
        types.addStretch()
        layout.addLayout(types)
        filters = QHBoxLayout()
        self.dataset_combo = ComboBox()
        self.dataset_combo.setMinimumWidth(170)
        self.dataset_combo.currentIndexChanged.connect(self.dataset_changed)
        filters.addWidget(self.dataset_combo)
        self.batch_combo = ComboBox()
        self.batch_combo.setMinimumWidth(150)
        self.batch_combo.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.batch_combo)
        self.date_combo = ComboBox()
        self.date_combo.setMinimumWidth(140)
        self.date_combo.addItems(["全部时间", "最近 7 天", "最近 30 天"])
        self.date_combo.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.date_combo)
        self.search = LineEdit()
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
        self.model = MediaModel(app.workspace, self)
        self.delegate = PhotoDelegate(self)
        self.view = list_view(self, self.model, self.delegate)
        self.view.doubleClicked.connect(lambda index: app.show_media(index.data(ROLE)))
        self.view.selectionModel().selectionChanged.connect(self.selection_changed)
        splitter.addWidget(self.view)
        side, box = panel("素材信息")
        side.setMaximumWidth(290)
        side.setMinimumWidth(250)
        self.details_panel = side
        self.selection_title = BodyLabel("尚未选择素材")
        self.selection_title.setWordWrap(True)
        box.addWidget(self.selection_title)
        self.selection_details = muted("")
        box.addWidget(self.selection_details)
        self.play_button = PushButton(FIF.VIDEO, "播放视频")
        self.play_button.clicked.connect(self.play_selected)
        box.addWidget(self.play_button)
        self.reveal_button = PushButton(FIF.FOLDER, "打开所在文件夹")
        self.reveal_button.clicked.connect(self.reveal_selected)
        box.addWidget(self.reveal_button)
        self.edit_button = PushButton("编辑信息")
        self.edit_button.clicked.connect(self.edit_selected)
        box.addWidget(self.edit_button)
        self.photo_actions = menu_button(
            "更多图片操作",
            [
                ("移动到其他批次", self.move_selected),
                ("重新裁剪", self.reprocess_selected),
            ],
        )
        box.addWidget(self.photo_actions)
        delete = PushButton(FIF.DELETE, "移入回收站")
        delete.clicked.connect(self.delete_selected)
        box.addWidget(delete)
        box.addStretch()
        splitter.addWidget(side)
        side.hide()
        splitter.setStretchFactor(0, 1)
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        self.count_label = muted("")
        self.model.total_changed.connect(self.update_summary)
        footer.addWidget(self.count_label)
        self.empty_label = muted("暂无匹配素材，可调整筛选、继续采集或导入图片。")
        footer.addWidget(self.empty_label, 1)
        self.model.total_changed.connect(lambda total: self.empty_label.setVisible(total == 0))
        footer.addWidget(muted("缩略图"))
        slider = Slider(Qt.Orientation.Horizontal)
        slider.setRange(170, 320)
        slider.setValue(230)
        slider.setFixedWidth(130)
        slider.valueChanged.connect(self.resize_cards)
        footer.addWidget(slider)
        layout.addLayout(footer)
        self.type_tabs.currentItemChanged.connect(self.reload)
        self.refresh()

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

    def selection_changed(self, *_):
        items = self.selected()
        self.details_panel.setVisible(bool(items))
        only_photos = bool(items) and all(item["media_type"] == "photo" for item in items)
        self.edit_button.setVisible(only_photos)
        self.photo_actions.setVisible(only_photos)
        self.play_button.setVisible(len(items) == 1 and items[0]["media_type"] == "video")
        self.reveal_button.setVisible(len(items) == 1)
        if not items:
            self.selection_title.setText("尚未选择素材")
            self.selection_details.setText(
                "双击图片查看大图，双击视频播放。\n按 Ctrl / ⌘ 或 Shift 可多选素材。"
            )
        elif len(items) > 1:
            self.selection_title.setText(f"已选择 {len(items)} 项素材")
            photos = sum(item["media_type"] == "photo" for item in items)
            self.selection_details.setText(f"{photos} 张图片 · {len(items) - photos} 段视频")
        else:
            item = items[0]
            self.selection_title.setText(item["sample_id"] or item["id"][:12].upper())
            dimensions = (
                f"视频 · {video_duration(item['duration_ms'])}"
                if item["media_type"] == "video"
                else f"图片 · {item['width']} × {item['height']}"
            )
            custom = json.loads(item["metadata"]).get("custom", {})
            self.selection_details.setText(
                f"{dimensions}\n{item['batch_name']}\n{item['created_at'][:19].replace('T', ' ')} UTC"
                f"\n\n{item['tags']}\n{item['note']}\n"
                + "\n".join(f"{key}：{value}" for key, value in custom.items())
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
