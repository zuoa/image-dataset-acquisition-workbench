from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QCamera
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SwitchButton,
    TextEdit,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from ..camera import CameraController
from .canvas import ImageCanvas
from .common import (
    ComboBox,
    DoubleSpinBox,
    FoldSection,
    FormDialog,
    MetadataEditor,
    SpinBox,
    confirm,
    heading,
    list_view,
    menu_button,
    muted,
    video_duration,
)
from .models import ROLE, PhotoDelegate, PhotoModel
from .theme import SoftCard as CardWidget


class CapturePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("capture")
        self.camera = CameraController(self)
        self.template = None
        self.templates = []
        self.datasets = []
        self.batches = []
        self.running = False
        self.pending_shot = False
        self.recording_context = None
        self.countdown_remaining = 0
        self.shot_timer = QTimer(self)
        self.shot_timer.timeout.connect(self.take_photo)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(20)
        header = QHBoxLayout()
        header.addWidget(heading("采集"), 1)
        self.mode_combo = ComboBox()
        self.mode_combo.addItems(["照片", "视频"])
        self.mode_combo.setFixedWidth(150)
        self.mode_combo.setToolTip("选择拍照或录制视频")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        header.addWidget(self.mode_combo)
        layout.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 12, 0)
        cl.setSpacing(12)
        status = QHBoxLayout()
        self.stream_status = muted("尚未连接设备")
        self.image_size = muted("— × —")
        self.recording_time = BodyLabel("●  00:00:00")
        self.recording_time.setStyleSheet("color: #CF354A; font-weight: 600;")
        self.recording_time.hide()
        status.addWidget(self.stream_status, 1)
        status.addWidget(self.recording_time)
        status.addWidget(self.image_size)
        cl.addLayout(status)
        self.canvas = ImageCanvas(editable=True)
        cl.addWidget(self.canvas, 1)
        toolbar = QHBoxLayout()
        draw = self.draw_button = PushButton(FIF.EDIT, "绘制热区")
        draw.setCheckable(True)
        draw.toggled.connect(self._draw_mode)
        toolbar.addWidget(draw)
        fit = PushButton("适应窗口")
        fit.clicked.connect(self.fit_canvas)
        toolbar.addWidget(fit)
        toolbar.addStretch()
        self.capture_count = muted("本批次 0 张")
        toolbar.addWidget(self.capture_count)
        cl.addLayout(toolbar)

        self.recent_model = PhotoModel(app.workspace, self)
        delegate = PhotoDelegate(self, compact=True)
        delegate.card_width = 175
        self.recent_view = list_view(self, self.recent_model, delegate, compact=True)
        self.recent_view.doubleClicked.connect(lambda i: app.show_photo(i.data(ROLE)))
        self.recent_model.total_changed.connect(self._update_capture_count)
        self.recent_view.hide()
        cl.addWidget(self.recent_view)
        self.video_hint = muted("录制完整画面（无麦克风音轨），按当前数据集和批次保存为 MP4。")
        self.video_hint.hide()
        cl.addWidget(self.video_hint)
        self.browse_videos = PushButton(FIF.VIDEO, "查看本数据集视频")
        self.browse_videos.clicked.connect(self.open_videos)
        self.browse_videos.hide()
        cl.addWidget(self.browse_videos, alignment=Qt.AlignmentFlag.AlignLeft)

        controls = QHBoxLayout()
        self.sample = LineEdit()
        self.sample.setPlaceholderText("样本编号（可选）")
        self.sample.setMaximumWidth(340)
        controls.addWidget(self.sample, 1)
        controls.addStretch()
        self.shoot = PrimaryPushButton(FIF.CAMERA, "拍照  Space")
        self.shoot.setMinimumSize(160, 48)
        self.shoot.clicked.connect(self.request_capture)
        controls.addWidget(self.shoot)
        cl.addLayout(controls)
        splitter.addWidget(center)

        scroll = self.options_scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(310)
        scroll.setMaximumWidth(340)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right = CardWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 20, 20, 20)
        rl.setSpacing(14)

        def section_title(text):
            label = BodyLabel(text)
            label.setObjectName("sectionTitle")
            rl.addWidget(label)
            return label

        section_title("保存到")
        self.dataset_combo = ComboBox()
        self.dataset_combo.setPlaceholderText("选择数据集")
        self.dataset_combo.currentIndexChanged.connect(self._dataset_changed)
        rl.addWidget(self.dataset_combo)
        batch_row = QHBoxLayout()
        self.batch_combo = ComboBox()
        self.batch_combo.setPlaceholderText("选择批次")
        self.batch_combo.currentIndexChanged.connect(self._batch_changed)
        batch_row.addWidget(self.batch_combo, 1)
        batch_row.addWidget(
            menu_button(
                "批次管理",
                [
                    ("新建批次", self.new_batch),
                    ("编辑批次", self.edit_batch),
                    ("删除批次", self.delete_batch),
                ],
                compact=True,
            )
        )
        rl.addLayout(batch_row)
        self.batch_info = muted("通过右侧菜单新建采集批次")
        rl.addWidget(self.batch_info)

        rl.addSpacing(8)
        section_title("相机")
        self.device_combo = ComboBox()
        self.device_combo.setPlaceholderText("未发现摄像头")
        self.device_combo.currentIndexChanged.connect(self.refresh_formats)
        rl.addWidget(self.device_combo)
        connection = QHBoxLayout()
        self.connect_button = PushButton("连接相机")
        self.connect_button.clicked.connect(self.connect_camera)
        connection.addWidget(self.connect_button, 1)
        connection.addWidget(
            menu_button(
                "相机操作",
                [
                    ("刷新设备", self.refresh_devices),
                    ("断开连接", self.disconnect_camera),
                ],
                compact=True,
            )
        )
        rl.addLayout(connection)

        self.camera_options = FoldSection("相机参数")
        box = self.camera_options.content_layout
        box.addWidget(muted("采集分辨率"))
        self.format_combo = ComboBox()
        self.format_combo.setPlaceholderText("连接设备后选择")
        box.addWidget(self.format_combo)
        self.exposure_auto = SwitchButton("自动曝光")
        self.exposure_auto.setOnText("自动曝光")
        self.exposure_auto.setOffText("手动曝光")
        self.exposure_auto.setChecked(True)
        self.exposure_auto.checkedChanged.connect(self.set_exposure)
        box.addWidget(self.exposure_auto)
        self.exposure = DoubleSpinBox()
        self.exposure.setRange(0.01, 1000)
        self.exposure.setValue(10)
        self.exposure.setSuffix(" ms 曝光")
        self.exposure.valueChanged.connect(self.set_exposure)
        box.addWidget(self.exposure)
        self.wb = ComboBox()
        self.wb.addItems(["自动白平衡", "日光", "阴天", "白炽灯"])
        self.wb.currentIndexChanged.connect(self.set_white_balance)
        box.addWidget(self.wb)
        self.focus = ComboBox()
        self.focus.addItems(["连续自动对焦", "近距离自动对焦", "手动对焦"])
        self.focus.currentIndexChanged.connect(self.set_focus)
        box.addWidget(self.focus)
        self.focus_distance = DoubleSpinBox()
        self.focus_distance.setRange(0, 1)
        self.focus_distance.setSingleStep(0.05)
        self.focus_distance.setSuffix(" 对焦距离")
        self.focus_distance.valueChanged.connect(self.set_focus)
        box.addWidget(self.focus_distance)
        rl.addWidget(self.camera_options)

        rl.addSpacing(8)
        self.crop_title = section_title("自动裁剪")
        self.template_combo = ComboBox()
        self.template_combo.addItem("不使用模板")
        self.template_combo.currentIndexChanged.connect(self.apply_template)
        rl.addWidget(self.template_combo)
        self.template_status = muted("未设置热区")
        rl.addWidget(self.template_status)
        self.crop_tools = FoldSection("热区管理")
        box = self.crop_tools.content_layout
        self.region_list = QListWidget()
        self.region_list.setFixedHeight(100)
        self.region_list.setStyleSheet(
            "QListWidget {border:1px solid #E0E6EC;border-radius:6px;background:white;}"
        )
        box.addWidget(self.region_list)
        name_region = PushButton("重命名热区")
        name_region.clicked.connect(self.rename_region)
        box.addWidget(name_region)
        row = QHBoxLayout()
        save = PushButton("保存模板")
        save.clicked.connect(self.save_template)
        clear = PushButton("清空")
        clear.clicked.connect(lambda: self.canvas.set_regions([]))
        row.addWidget(save)
        row.addWidget(clear)
        box.addLayout(row)
        rl.addWidget(self.crop_tools)

        self.shooting_options = FoldSection("定时拍摄")
        box = self.shooting_options.content_layout
        box.addWidget(muted("拍照倒计时"))
        self.countdown = SpinBox()
        self.countdown.setRange(0, 30)
        self.countdown.setSuffix(" 秒")
        box.addWidget(self.countdown)
        box.addWidget(muted("连续采集间隔"))
        self.interval = SpinBox()
        self.interval.setRange(1, 3600)
        self.interval.setValue(5)
        self.interval.setSuffix(" 秒")
        box.addWidget(self.interval)
        self.interval_button = PushButton("定时采集")
        self.interval_button.clicked.connect(self.toggle_interval)
        box.addWidget(self.interval_button)
        rl.addWidget(self.shooting_options)

        self.experiment_options = FoldSection("实验信息")
        box = self.experiment_options.content_layout
        self.tags = LineEdit()
        self.tags.setPlaceholderText("标签，逗号分隔")
        box.addWidget(self.tags)
        self.note = TextEdit()
        self.note.setPlaceholderText("实验条件或备注")
        self.note.setFixedHeight(88)
        box.addWidget(self.note)
        self.custom = MetadataEditor()
        box.addWidget(self.custom)
        rl.addWidget(self.experiment_options)
        rl.addStretch()
        scroll.setWidget(right)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([850, 330])

        self.camera.preview.connect(self.canvas.set_image)
        self.camera.status.connect(self.camera_status)
        self.camera.connected.connect(self.camera_connected)
        self.camera.devices_changed.connect(self.refresh_devices)
        self.camera.recording_changed.connect(self.update_capture_controls)
        self.camera.recording_duration.connect(
            lambda duration: self.recording_time.setText("●  " + video_duration(duration))
        )
        self.camera.video_finished.connect(self._video_finished)
        self.camera.video_error.connect(self._video_failed)
        self.canvas.regions_changed.connect(self.regions_changed)
        self.canvas.dimensions_changed.connect(self.dimensions_changed)
        self.refresh_devices()
        self.refresh()
        self.camera_connected(False)

    def dataset_id(self):
        i = self.dataset_combo.currentIndex()
        return self.datasets[i]["id"] if 0 <= i < len(self.datasets) else None

    def batch_id(self):
        i = self.batch_combo.currentIndex()
        return self.batches[i]["id"] if 0 <= i < len(self.batches) else None

    def select_dataset(self, dataset_id, batch_id=None):
        for i, data in enumerate(self.datasets):
            if data["id"] == dataset_id:
                self.dataset_combo.setCurrentIndex(i)
                if batch_id:
                    for index, batch in enumerate(self.batches):
                        if batch["id"] == batch_id:
                            self.batch_combo.setCurrentIndex(index)
                            break
                return

    def refresh(self):
        previous = self.dataset_id()
        self.datasets = self.app.workspace.datasets(include_archived=False)
        self.dataset_combo.blockSignals(True)
        self.dataset_combo.clear()
        self.dataset_combo.addItems([d["name"] for d in self.datasets])
        self.dataset_combo.setCurrentIndex(
            next((i for i, d in enumerate(self.datasets) if d["id"] == previous), 0 if self.datasets else -1)
        )
        self.dataset_combo.blockSignals(False)
        self._dataset_changed(preserve=True)

    def _dataset_changed(self, *_, preserve=False):
        previous_batch = self.batch_id() if preserve else None
        previous_template = self.template.get("id") if preserve and self.template else None
        self.stop_interval()
        dataset_id = self.dataset_id()
        self.batches = self.app.workspace.batches(dataset_id) if dataset_id else []
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        self.batch_combo.addItems([b["name"] for b in self.batches])
        self.batch_combo.setCurrentIndex(
            next(
                (i for i, b in enumerate(self.batches) if b["id"] == previous_batch),
                0 if self.batches else -1,
            )
        )
        self.batch_combo.blockSignals(False)
        if not preserve:
            self._batch_changed()
        self.templates = self.app.workspace.templates(dataset_id) if dataset_id else []
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem("不使用模板")
        self.template_combo.addItems([f"{t['name']} · v{t['version']}" for t in self.templates])
        chosen = next((i + 1 for i, t in enumerate(self.templates) if t["id"] == previous_template), 0)
        self.template_combo.setCurrentIndex(chosen)
        self.template_combo.blockSignals(False)
        if not preserve:
            self.apply_template(0)
        self.batch_info.setVisible(not self.batch_id())
        self.refresh_recent()

    def _batch_changed(self, *_):
        self.stop_interval()
        i = self.batch_combo.currentIndex()
        defaults = json.loads(self.batches[i]["defaults"]) if 0 <= i < len(self.batches) else {}
        self.sample.setText(defaults.get("sample_id", ""))
        self.tags.setText(defaults.get("tags", ""))
        self.note.setPlainText(defaults.get("note", ""))
        self.custom.set_value(defaults.get("custom", {}))
        self.batch_info.setText("" if self.batch_id() else "通过右侧菜单新建采集批次")
        self.batch_info.setVisible(not self.batch_id())
        self.refresh_recent()

    def refresh_recent(self):
        if self.dataset_id():
            self.recent_model.reload(dataset_id=self.dataset_id(), batch_id=self.batch_id())
        else:
            self.recent_model.reload(dataset_id="__none__")
        self._update_capture_count()

    @property
    def video_mode(self):
        return self.mode_combo.currentIndex() == 1

    def _update_capture_count(self, *_):
        if self.video_mode:
            total = len(
                self.app.workspace.videos(
                    dataset_id=self.dataset_id() or "__none__", batch_id=self.batch_id()
                )
            )
            self.capture_count.setText(f"本批次 {total:,} 段视频")
        else:
            self.capture_count.setText(f"本批次 {self.recent_model.total:,} 张")
        self.recent_view.setVisible(not self.video_mode and self.recent_model.total > 0)

    def _mode_changed(self, *_):
        self.stop_interval()
        for widget in (
            self.draw_button,
            self.crop_title,
            self.template_combo,
            self.template_status,
            self.crop_tools,
            self.shooting_options,
        ):
            widget.setVisible(not self.video_mode)
        self.canvas.editable = not self.video_mode
        self.canvas.show_regions = not self.video_mode
        self.canvas.draw_mode = False
        self.draw_button.setChecked(False)
        self.canvas.update()
        self.video_hint.setVisible(self.video_mode)
        self.browse_videos.setVisible(self.video_mode)
        self._update_capture_count()
        self.update_capture_controls()

    def update_capture_controls(self):
        busy = self.camera.video_busy
        saving_photo = self.camera.saving_photo
        active = bool(self.camera.camera and self.camera.camera.isActive())
        self.options_scroll.setEnabled(not busy and not saving_photo)
        self.mode_combo.setEnabled(not busy and not saving_photo)
        self.sample.setEnabled(not busy)
        self.browse_videos.setEnabled(not busy)
        self.recording_time.setVisible(busy)
        self.app.navigationInterface.setEnabled(not busy and not self.app.runner.busy)
        self.shoot.setEnabled(
            (active or busy) and not self.app.runner.busy and not self.camera.recording_stopping
        )
        self.shoot.setText(
            "正在保存视频…"
            if self.camera.recording_stopping
            else "停止并保存  Space"
            if busy
            else "开始录制  Space"
            if self.video_mode
            else "拍照  Space"
        )
        self.shoot.setIcon(FIF.VIDEO if self.video_mode else FIF.CAMERA)
        self.interval_button.setEnabled(active and not self.video_mode)

    def open_videos(self):
        self.app.open_library(self.dataset_id(), "video", self.batch_id())

    def request_capture(self):
        if self.video_mode:
            self.toggle_recording()
        else:
            self.request_photo()

    def toggle_recording(self):
        if self.camera.video_busy:
            self.camera.stop_recording()
            return
        if self.app.runner.busy:
            return
        path = None
        try:
            if not self.dataset_id() or not self.batch_id():
                raise ValueError("请先选择数据集并创建采集批次")
            if shutil.disk_usage(self.app.workspace.root).free < 100 * 1024 * 1024:
                raise ValueError("剩余磁盘空间不足 100 MB，请释放空间后再录制")
            self.recording_context = dict(
                dataset_id=self.dataset_id(),
                batch_id=self.batch_id(),
                sample_id=self.sample.text().strip(),
                tags=self.tags.text().strip(),
                note=self.note.toPlainText(),
                metadata=dict(
                    custom=self.custom.value(),
                    camera=self.camera.metadata(),
                    started_at=datetime.now().astimezone().isoformat(),
                ),
            )
            path = self.app.workspace.new_recording_path()
            self.recording_context["path"] = str(path)
            self.camera.start_recording(path)
        except Exception as error:
            self.recording_context = None
            if path:
                shutil.rmtree(path.parent, ignore_errors=True)
            self.app.notify(str(error), error=True)

    def _video_finished(self, path, duration):
        context = self.recording_context
        self.recording_context = None
        if not context:
            return
        context.pop("path", None)

        def save(cancel, progress):
            try:
                return self.app.workspace.add_video(path, duration_ms=duration, **context)
            except Exception as error:
                # Preserve a finalized video even when metadata cannot be committed.
                recovery = self.app.workspace.root / "recovered-recordings" / Path(path).parent.name
                recovery.mkdir(parents=True, exist_ok=True)
                shutil.move(path, recovery / "recording.mp4")
                (recovery / "metadata.json").write_text(
                    json.dumps(context | dict(duration_ms=duration), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                raise OSError(f"视频归档失败，文件保留于 {recovery}：{error}") from error

        def done(_):
            self.app.refresh()
            self.app.feedback.play()
            self.app.notify(f"录制完成 · {video_duration(duration)} 视频已保存")

        self.app.start_task("正在保存视频与实验信息…", save, done)

    def _video_failed(self, message):
        if self.recording_context:
            path = Path(self.recording_context["path"])
            shutil.rmtree(path.parent, ignore_errors=True)
        self.recording_context = None
        self.app.notify("录制失败：" + message, error=True)

    def _batch_dialog(self, data=None):
        values = dict(name=data["name"] if data else datetime.now().strftime("采集 %m-%d %H:%M"))
        if data:
            defaults = json.loads(data["defaults"])
            values.update(defaults)
        dialog = FormDialog(
            self,
            "编辑采集批次" if data else "新建采集批次",
            [
                ("name", "批次名称", False),
                ("sample_id", "默认样本编号", False),
                ("tags", "默认标签", False),
                ("note", "实验条件 / 备注", True),
                ("custom", "自定义字段", True),
            ],
            values,
        )
        if dialog.exec():
            values = dialog.values()
            name = values.pop("name")
            batch_id = self.app.workspace.save_batch(
                self.dataset_id(), name, values, data["id"] if data else None
            )
            self._dataset_changed(preserve=True)
            self.batch_combo.setCurrentIndex(
                next(i for i, b in enumerate(self.batches) if b["id"] == batch_id)
            )
            self._batch_changed()

    def new_batch(self):
        if not self.dataset_id():
            self.app.notify("请先在数据集页面新建数据集", error=True)
            return
        self._batch_dialog()

    def edit_batch(self):
        i = self.batch_combo.currentIndex()
        if 0 <= i < len(self.batches):
            self._batch_dialog(self.batches[i])

    def delete_batch(self):
        if self.batch_id() and confirm(
            self, "删除当前批次？", "批次及其图片、视频将移入回收站，可随时恢复。", True
        ):
            self.app.workspace.trash("batch", [self.batch_id()])
            self.app.refresh()

    def refresh_devices(self):
        current = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        devices = self.camera.devices()
        for device in devices:
            self.device_combo.addItem(device.description(), userData=bytes(device.id()).hex())
        i = next((i for i, d in enumerate(devices) if bytes(d.id()).hex() == current), 0)
        self.device_combo.setCurrentIndex(i if devices else -1)
        self.device_combo.blockSignals(False)
        self.refresh_formats()

    def refresh_formats(self, *_):
        self.format_combo.clear()
        formats = self.camera.formats(self.device_combo.currentIndex())
        for f in formats:
            self.format_combo.addItem(
                f"{f.resolution().width()} × {f.resolution().height()} · {f.maxFrameRate():g} fps · {f.pixelFormat().name.replace('Format_', '')}"
            )

    def connect_camera(self):
        if self.camera.video_busy:
            return
        self.stop_interval()
        self.camera.open(self.device_combo.currentIndex(), self.format_combo.currentIndex())

    def disconnect_camera(self):
        self.stop_interval()
        self.camera.stop()
        self.canvas.set_image(QImage())
        self.stream_status.setText("●  已断开设备")

    def camera_status(self, message):
        self.stream_status.setText("●  " + message)

    def camera_connected(self, active):
        self.shoot.setEnabled(active and not self.app.runner.busy)
        self.interval_button.setEnabled(active)
        camera = self.camera.camera
        if not active:
            self.stop_interval()
        support = camera.supportedFeatures() if active and camera else QCamera.Feature(0)
        self.exposure_auto.setEnabled(bool(support & QCamera.Feature.ManualExposureTime))
        self.exposure.setEnabled(
            bool(support & QCamera.Feature.ManualExposureTime) and not self.exposure_auto.isChecked()
        )
        self.wb.setEnabled(
            active
            and bool(camera)
            and camera.isWhiteBalanceModeSupported(QCamera.WhiteBalanceMode.WhiteBalanceAuto)
        )
        self.focus.setEnabled(
            active and bool(camera) and camera.isFocusModeSupported(QCamera.FocusMode.FocusModeAuto)
        )
        self.focus_distance.setEnabled(bool(support & QCamera.Feature.FocusDistance))
        self.connect_button.setText("重新连接" if active else "连接相机")
        if active:
            self.stream_status.setText("●  已连接 · " + camera.cameraDevice().description())
            for index, mode in enumerate(
                [
                    QCamera.WhiteBalanceMode.WhiteBalanceAuto,
                    QCamera.WhiteBalanceMode.WhiteBalanceSunlight,
                    QCamera.WhiteBalanceMode.WhiteBalanceCloudy,
                    QCamera.WhiteBalanceMode.WhiteBalanceTungsten,
                ]
            ):
                self.wb.setItemEnabled(index, camera.isWhiteBalanceModeSupported(mode))
            for index, mode in enumerate(
                [
                    QCamera.FocusMode.FocusModeAuto,
                    QCamera.FocusMode.FocusModeAutoNear,
                    QCamera.FocusMode.FocusModeManual,
                ]
            ):
                self.focus.setItemEnabled(index, camera.isFocusModeSupported(mode))
        self.update_capture_controls()

    def set_exposure(self, *_):
        camera = self.camera.camera
        if camera and camera.supportedFeatures() & QCamera.Feature.ManualExposureTime:
            auto = self.exposure_auto.isChecked()
            camera.setExposureMode(
                QCamera.ExposureMode.ExposureAuto if auto else QCamera.ExposureMode.ExposureManual
            )
            self.exposure.setEnabled(not auto)
            if auto:
                camera.setAutoExposureTime()
            else:
                camera.setManualExposureTime(self.exposure.value() / 1000)

    def set_white_balance(self, index):
        camera = self.camera.camera
        modes = [
            QCamera.WhiteBalanceMode.WhiteBalanceAuto,
            QCamera.WhiteBalanceMode.WhiteBalanceSunlight,
            QCamera.WhiteBalanceMode.WhiteBalanceCloudy,
            QCamera.WhiteBalanceMode.WhiteBalanceTungsten,
        ]
        if camera and 0 <= index < len(modes) and camera.isWhiteBalanceModeSupported(modes[index]):
            camera.setWhiteBalanceMode(modes[index])

    def set_focus(self, *_):
        camera = self.camera.camera
        modes = [
            QCamera.FocusMode.FocusModeAuto,
            QCamera.FocusMode.FocusModeAutoNear,
            QCamera.FocusMode.FocusModeManual,
        ]
        if camera:
            mode = modes[self.focus.currentIndex()]
            if camera.isFocusModeSupported(mode):
                camera.setFocusMode(mode)
            if (
                mode == QCamera.FocusMode.FocusModeManual
                and camera.supportedFeatures() & QCamera.Feature.FocusDistance
            ):
                camera.setFocusDistance(self.focus_distance.value())

    def dimensions_changed(self, width, height):
        self.image_size.setText(f"{width} × {height}" if width else "— × —")
        if self.template and (width, height) != (self.template["width"], self.template["height"]):
            self.template = None
            self.template_status.setText("分辨率已变化，请重新选择模板或标定热区")
            self.template_combo.blockSignals(True)
            self.template_combo.setCurrentIndex(0)
            self.template_combo.blockSignals(False)

    def regions_changed(self, regions):
        if self.template and regions != self.template["regions"]:
            self.template = None
        self.region_list.clear()
        self.region_list.addItems([f"{r['name']}   {r['w']} × {r['h']}" for r in regions])
        self.template_status.setText(
            f"{len(regions)} 个热区 · 原图与裁剪图一并保存" if regions else "未设置热区"
        )

    def rename_region(self):
        index = self.region_list.currentRow()
        if index < 0:
            index = self.canvas.selected
        if index < 0:
            self.app.notify("请先选中一个热区", error=True)
            return
        regions = [dict(r) for r in self.canvas.regions]
        dialog = FormDialog(self, "重命名热区", [("name", "热区名称", False)], regions[index])
        if dialog.exec():
            name = dialog.values()["name"]
            if any(r["name"] == name for i, r in enumerate(regions) if i != index):
                self.app.notify("热区名称不能重复", error=True)
                return
            regions[index]["name"] = name
            self.canvas.set_regions(regions)

    def save_template(self):
        if not self.dataset_id() or self.canvas.image.isNull() or not self.canvas.regions:
            self.app.notify("请选择数据集，在画面上绘制热区后保存模板", error=True)
            return
        dialog = FormDialog(
            self,
            "保存热区模板",
            [("name", "模板名称", False)],
            {"name": self.template["name"] if self.template else "采集热区"},
        )
        if dialog.exec():
            size = self.canvas.source_size
            self.template = self.app.workspace.save_template(
                self.dataset_id(), dialog.values()["name"], size.width(), size.height(), self.canvas.regions
            )
            self._dataset_changed(preserve=True)
            self.app.notify("模板已保存；同名模板自动创建新版本")

    def apply_template(self, index):
        if index <= 0:
            self.template = None
            self.canvas.set_regions([])
            return
        template = self.templates[index - 1]
        image = self.canvas.image
        if image.isNull() or self.canvas.source_size.toTuple() != (template["width"], template["height"]):
            self.app.notify("请连接相机并使用与模板一致的图像分辨率", error=True)
            self.template_combo.setCurrentIndex(0)
            return
        self.template = template
        self.canvas.set_regions(template["regions"])

    def _draw_mode(self, enabled):
        self.canvas.draw_mode = enabled
        if enabled:
            self.crop_tools.set_expanded(True)
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def fit_canvas(self):
        from PySide6.QtCore import QPointF

        self.canvas.zoom = 1
        self.canvas.pan = QPointF()
        self.canvas.update()

    def request_photo(self):
        if self.video_mode or self.camera.video_busy:
            return
        if self.countdown_timer.isActive():
            self.countdown_timer.stop()
            self.shoot.setText("拍照  Space")
            return
        if self.app.runner.busy:
            return
        seconds = self.countdown.value()
        if seconds:
            self.countdown_remaining = seconds
            self.shoot.setText(f"{seconds} 秒 · 点击取消")
            self.countdown_timer.start()
        else:
            self.take_photo()

    def _countdown_tick(self):
        self.countdown_remaining -= 1
        if self.countdown_remaining <= 0:
            self.countdown_timer.stop()
            self.shoot.setText("拍照  Space")
            self.take_photo()
        else:
            self.shoot.setText(f"{self.countdown_remaining} 秒 · 点击取消")

    def toggle_interval(self):
        if self.running:
            self.stop_interval()
        elif self.batch_id():
            self.running = True
            self.interval_button.setText("停止采集")
            self.shot_timer.start(self.interval.value() * 1000)
            self.take_photo()
        else:
            self.app.notify("请先选择采集批次", error=True)

    def stop_interval(self):
        self.running = False
        self.shot_timer.stop()
        self.countdown_timer.stop()
        if hasattr(self, "interval_button"):
            self.interval_button.setText("定时采集")
            self.update_capture_controls()

    def take_photo(self):
        if self.app.runner.busy or self.video_mode or self.camera.video_busy:
            return  # Slow storage applies backpressure; no unbounded frame queue.
        try:
            if not self.dataset_id() or not self.batch_id():
                raise ValueError("请先选择数据集并创建采集批次")
            image = self.camera.snapshot()
            custom = self.custom.value()
            kwargs = dict(
                sample_id=self.sample.text().strip(),
                tags=self.tags.text().strip(),
                note=self.note.toPlainText(),
                custom=custom,
                camera=self.camera.metadata(),
                regions=[dict(r) for r in self.canvas.regions],
                template=self.template,
                png_compress_level=1,
            )
            dataset_id, batch_id = self.dataset_id(), self.batch_id()

            def save(cancel, progress):
                # Let the single in-flight preview conversion finish off the GUI
                # thread before encoding. New conversions pause until this save ends.
                self.camera.frame_pool.waitForDone()
                rgb = image.convertToFormat(QImage.Format.Format_RGB888)
                with Image.frombytes(
                    "RGB",
                    (rgb.width(), rgb.height()),
                    bytes(rgb.constBits()),
                    "raw",
                    "RGB",
                    rgb.bytesPerLine(),
                ) as frame:
                    return self.app.workspace.add_photo(frame, dataset_id, batch_id, **kwargs)

            self.app.start_task("正在保存原图与裁剪图…", save, self._saved, photo_capture=True)
        except Exception as error:
            self.stop_interval()
            self.app.notify(str(error), error=True)

    def _saved(self, _):
        self.refresh_recent()
        self.app.home.refresh()
        self.app.library.refresh()
        self.app.feedback.play()
        self.app.notify("采集成功 · 原图与裁剪图已保存")
