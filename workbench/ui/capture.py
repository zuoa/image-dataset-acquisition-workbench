from __future__ import annotations

import json
import math
import re
import shutil
from math import isfinite
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QCamera
from PySide6.QtWidgets import QFrame, QHBoxLayout, QListWidget, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    Slider,
    SwitchButton,
    TextEdit,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from ..camera import CameraController
from ..mvs import DOWNLOAD_URL, SettingsChange
from ..timeutils import display_now
from .canvas import ImageCanvas
from .common import (
    ComboBox,
    DoubleSpinBox,
    FoldSection,
    FormDialog,
    IconBadge,
    MetadataEditor,
    SpinBox,
    confirm,
    divider,
    heading,
    icon_button,
    list_view,
    menu_button,
    muted,
    video_duration,
)
from .models import ROLE, PhotoDelegate, PhotoModel
from .theme import (
    BLUE,
    BORDER,
    CANDLING_BG,
    CANDLING_BORDER,
    INK,
    INK_SECONDARY,
    SURFACE,
    SURFACE_HOVER,
    WARNING,
    WARNING_BG,
    WARNING_BORDER,
    StatusPill,
)
from .theme import SoftCard as CardWidget


def increment_sample_id(value: str) -> str:
    """Increment trailing digits in a sample ID while preserving zero padding."""
    if not value:
        return value
    match = re.search(r"(\d+)$", value)
    if not match:
        return value
    digits = match.group(1)
    next_num = str(int(digits) + 1).zfill(len(digits))
    return value[: match.start(1)] + next_num


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
        self._native_exposure = None
        self._applying_settings = False
        self._active_device_index = -1
        self.countdown_remaining = 0
        self.interval_shots = 0

        self.shot_timer = QTimer(self)
        self.shot_timer.timeout.connect(self.take_photo)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)
        self.interval_hud_timer = QTimer(self)
        self.interval_hud_timer.setInterval(500)
        self.interval_hud_timer.timeout.connect(self._update_interval_hud)

        self._exposure_commit_timer = QTimer(self)
        self._exposure_commit_timer.setSingleShot(True)
        self._exposure_commit_timer.setInterval(120)
        self._exposure_commit_timer.timeout.connect(self._commit_exposure_slider)
        self._gain_commit_timer = QTimer(self)
        self._gain_commit_timer.setSingleShot(True)
        self._gain_commit_timer.setInterval(120)
        self._gain_commit_timer.timeout.connect(self._commit_gain_slider)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(heading("采集"), 1)
        self.mode_combo = ComboBox()
        self.mode_combo.addItems(["照片", "视频"])
        self.mode_combo.setFixedWidth(136)
        self.mode_combo.setToolTip("选择拍照或录制视频")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        header.addWidget(self.mode_combo)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(10)

        status = QHBoxLayout()
        status.setSpacing(8)
        self.stream_status = StatusPill("尚未连接设备", "neutral", dot=True)
        self.candling_badge = StatusPill("照蛋模式", "candling", dot=True)
        self.candling_badge.hide()
        self.recording_time = StatusPill("00:00:00", "danger", dot=True)
        self.recording_time.hide()
        self.image_size = StatusPill("— × —", "neutral", dot=False)
        status.addWidget(self.stream_status)
        status.addWidget(self.candling_badge)
        status.addStretch(1)
        status.addWidget(self.recording_time)
        status.addWidget(self.image_size)
        cl.addLayout(status)

        self.canvas = ImageCanvas(editable=True)
        cl.addWidget(self.canvas, 1)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        draw = self.draw_button = PushButton(FIF.EDIT, "绘制热区")
        draw.setCheckable(True)
        draw.toggled.connect(self._draw_mode)
        toolbar.addWidget(draw)
        fit = PushButton(FIF.FIT_PAGE, "适应窗口")
        fit.clicked.connect(self.fit_canvas)
        toolbar.addWidget(fit)
        toolbar.addStretch()
        self.capture_count = muted("本批次 0 张")
        toolbar.addWidget(self.capture_count)
        cl.addLayout(toolbar)

        self.recent_model = PhotoModel(app.workspace, self)
        delegate = PhotoDelegate(self, compact=True)
        delegate.card_width = 166
        self.recent_view = list_view(self, self.recent_model, delegate, compact=True)
        self.recent_view.doubleClicked.connect(self._open_recent_photo)
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
        controls.setSpacing(10)
        self.sample = LineEdit()
        self.sample.setPlaceholderText("样本编号（可选，如 EGG-001）")
        self.sample.setMaximumWidth(280)
        controls.addWidget(self.sample, 1)
        self.auto_increment = CheckBox("自动递增")
        self.auto_increment.setToolTip("拍照保存后自动递增样本编号末尾数字（如 EGG-001 → EGG-002）")
        self.auto_increment.setChecked(
            bool(self.app.preferences.value("capture/sample_auto_increment", False, type=bool))
        )
        self.auto_increment.stateChanged.connect(self._auto_increment_changed)
        controls.addWidget(self.auto_increment)
        controls.addStretch()
        self.shoot = PrimaryPushButton(FIF.CAMERA, "拍照  Space")
        self.shoot.setMinimumSize(164, 44)
        self.shoot.clicked.connect(self.request_capture)
        controls.addWidget(self.shoot)
        cl.addLayout(controls)
        splitter.addWidget(center)

        scroll = self.options_scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(316)
        scroll.setMaximumWidth(356)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right = CardWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(18, 18, 18, 18)
        rl.setSpacing(10)

        def section_title(text, icon=None):
            wrapper = QWidget()
            row_layout = QHBoxLayout(wrapper)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            if icon is not None:
                row_layout.addWidget(IconBadge(icon, INK_SECONDARY, size=14, parent=wrapper))
            label = BodyLabel(text)
            label.setObjectName("sectionTitle")
            row_layout.addWidget(label, 1)
            rl.addWidget(wrapper)
            return wrapper

        section_title("保存到", FIF.FOLDER)
        self.dataset_combo = ComboBox()
        self.dataset_combo.setPlaceholderText("选择数据集")
        self.dataset_combo.currentIndexChanged.connect(self._dataset_changed)
        rl.addWidget(self.dataset_combo)
        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)
        self.batch_combo = ComboBox()
        self.batch_combo.setPlaceholderText("选择批次")
        self.batch_combo.currentIndexChanged.connect(self._batch_changed)
        batch_row.addWidget(self.batch_combo, 1)
        self.new_batch_button = icon_button(FIF.ADD, "新建批次", self.new_batch, soft=True)
        batch_row.addWidget(self.new_batch_button)
        batch_row.addWidget(
            menu_button(
                "更多批次操作",
                [
                    (FIF.EDIT, "编辑批次", self.edit_batch),
                    (FIF.DELETE, "删除批次", self.delete_batch),
                ],
                compact=True,
            )
        )
        rl.addLayout(batch_row)
        self.batch_info = muted("点击右侧「＋」新建采集批次")
        rl.addWidget(self.batch_info)

        rl.addWidget(divider())
        section_title("相机", FIF.CAMERA)
        self.device_combo = ComboBox()
        self.device_combo.setPlaceholderText("未发现摄像头")
        self.device_combo.currentIndexChanged.connect(self.refresh_formats)
        rl.addWidget(self.device_combo)
        connection = QHBoxLayout()
        connection.setSpacing(6)
        self.connect_button = PushButton(FIF.CONNECT, "连接相机")
        self.connect_button.setProperty("softAction", True)
        self.connect_button.clicked.connect(self.connect_camera)
        connection.addWidget(self.connect_button, 1)
        self.refresh_devices_button = icon_button(FIF.SYNC, "刷新设备列表", self.refresh_devices)
        connection.addWidget(self.refresh_devices_button)
        self.disconnect_button = icon_button(FIF.CLOSE, "断开连接", self.disconnect_camera, danger=True)
        self.disconnect_button.setEnabled(False)
        connection.addWidget(self.disconnect_button)
        rl.addLayout(connection)

        self._mvs_warning_dismissed = bool(
            self.app.preferences.value("capture/mvs_warning_dismissed", False, type=bool)
        )
        self.mvs_warning = QFrame()
        self.mvs_warning.setObjectName("mvsWarning")
        self.mvs_warning.setStyleSheet(
            f"QFrame#mvsWarning {{ background: {WARNING_BG}; border: 1px solid {WARNING_BORDER}; "
            "border-radius: 10px; }"
        )
        warning_layout = QVBoxLayout(self.mvs_warning)
        warning_layout.setContentsMargins(10, 8, 10, 8)
        warning_layout.setSpacing(6)
        self.mvs_warning_label = muted("")
        self.mvs_warning_label.setStyleSheet(f"QLabel {{ color: {WARNING}; background: transparent; }}")
        warning_layout.addWidget(self.mvs_warning_label)
        warning_actions = QHBoxLayout()
        warning_actions.setSpacing(6)
        self.mvs_download_button = PushButton(FIF.DOWNLOAD, "官网下载")
        self.mvs_download_button.setProperty("compactControl", True)
        self.mvs_download_button.clicked.connect(lambda: self.app.open_url(DOWNLOAD_URL))
        self.mvs_settings_button = PushButton(FIF.SETTING, "配置路径")
        self.mvs_settings_button.setProperty("compactControl", True)
        self.mvs_settings_button.clicked.connect(self.app.open_settings)
        self.mvs_dismiss_button = icon_button(
            FIF.CLOSE, "忽略此提示（仅用普通摄像头）", self._dismiss_mvs_warning
        )
        self.mvs_dismiss_button.setFixedSize(32, 32)
        warning_actions.addWidget(self.mvs_download_button, 1)
        warning_actions.addWidget(self.mvs_settings_button, 1)
        warning_actions.addWidget(self.mvs_dismiss_button)
        warning_layout.addLayout(warning_actions)
        self.mvs_warning.hide()
        rl.addWidget(self.mvs_warning)

        self.camera_options = FoldSection("相机参数", icon=FIF.SETTING)
        box = self.camera_options.content_layout
        box.addWidget(muted("采集分辨率"))
        self.format_combo = ComboBox()
        self.format_combo.setPlaceholderText("连接设备后选择")
        self.format_combo.activated.connect(self._format_selected)
        box.addWidget(self.format_combo)

        self.exposure_auto = SwitchButton("自动曝光")
        self.exposure_auto.setOnText("自动曝光")
        self.exposure_auto.setOffText("手动曝光")
        self.exposure_auto.setChecked(True)
        self.exposure_auto.checkedChanged.connect(self.set_exposure)
        box.addWidget(self.exposure_auto)

        box.addWidget(muted("曝光时间"))
        self.exposure = DoubleSpinBox()
        self.exposure.setRange(0.01, 1000)
        self.exposure.setValue(10)
        self.exposure.setSuffix(" ms")
        self.exposure.valueChanged.connect(self.set_exposure)
        box.addWidget(self.exposure)
        self.exposure_slider = Slider(Qt.Orientation.Horizontal)
        self.exposure_slider.setRange(0, 1000)
        self.exposure_slider.sliderMoved.connect(self._exposure_slider_moved)
        self.exposure_slider.sliderReleased.connect(self._commit_exposure_slider)
        self.exposure_slider.valueChanged.connect(self._exposure_slider_changed)
        box.addWidget(self.exposure_slider)

        self.gain_label = muted("模拟增益")
        box.addWidget(self.gain_label)
        self.gain = DoubleSpinBox()
        self.gain.setSuffix(" dB")
        self.gain.setKeyboardTracking(False)
        self.gain.valueChanged.connect(self.set_gain)
        box.addWidget(self.gain)
        self.gain_slider = Slider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(0, 240)
        self.gain_slider.sliderMoved.connect(self._gain_slider_moved)
        self.gain_slider.sliderReleased.connect(self._commit_gain_slider)
        self.gain_slider.valueChanged.connect(self._gain_slider_changed)
        box.addWidget(self.gain_slider)

        self.candling_card = QFrame()
        self.candling_card.setStyleSheet(
            f"QFrame {{ background: {CANDLING_BG}; border: 1px solid {CANDLING_BORDER}; border-radius: 12px; }}"
        )
        candling_layout = QVBoxLayout(self.candling_card)
        candling_layout.setContentsMargins(12, 10, 12, 10)
        candling_layout.setSpacing(8)
        self.candling_mode = SwitchButton("照蛋模式")
        self.candling_mode.setOnText("照蛋模式")
        self.candling_mode.checkedChanged.connect(self.set_candling_mode)
        candling_layout.addWidget(self.candling_mode)
        self.candling_preset_hint = muted("尚未保存本机预设")
        candling_layout.addWidget(self.candling_preset_hint)
        self.save_candling = PushButton(FIF.SAVE, "保存照蛋预设")
        self.save_candling.clicked.connect(self.save_candling_preset)
        candling_layout.addWidget(self.save_candling)
        box.addWidget(self.candling_card)

        self.wb_label = muted("白平衡")
        box.addWidget(self.wb_label)
        self.wb = ComboBox()
        self.wb.addItems(["自动白平衡", "日光", "阴天", "白炽灯"])
        self.wb.currentIndexChanged.connect(self.set_white_balance)
        box.addWidget(self.wb)

        self.focus_label = muted("对焦模式")
        box.addWidget(self.focus_label)
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

        self._uvc_controls = [
            self.exposure_auto,
            self.wb_label,
            self.wb,
            self.focus_label,
            self.focus,
            self.focus_distance,
        ]
        rl.addWidget(self.camera_options)

        rl.addWidget(divider())
        self.crop_title = section_title("自动裁剪", FIF.CLIPPING_TOOL)
        self.template_combo = ComboBox()
        self.template_combo.addItem("不使用模板")
        self.template_combo.currentIndexChanged.connect(self.apply_template)
        rl.addWidget(self.template_combo)
        self.template_status = muted("未设置热区")
        rl.addWidget(self.template_status)

        self.crop_tools = FoldSection("热区管理", icon=FIF.LAYOUT)
        box = self.crop_tools.content_layout
        self.region_list = QListWidget()
        self.region_list.setFixedHeight(104)
        self.region_list.setStyleSheet(
            f"""
            QListWidget {{
                border: 1px solid {BORDER};
                border-radius: 10px;
                background: {SURFACE};
                color: {INK};
                padding: 4px;
            }}
            QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
            QListWidget::item:hover {{ background: {SURFACE_HOVER}; }}
            QListWidget::item:selected {{ background: {BLUE}; color: white; }}
            """
        )
        self.region_list.currentRowChanged.connect(self._region_row_changed)
        box.addWidget(self.region_list)
        region_actions = QHBoxLayout()
        region_actions.setSpacing(6)
        self.rename_region_button = PushButton(FIF.EDIT, "重命名")
        self.rename_region_button.clicked.connect(self.rename_region)
        self.delete_region_button = PushButton(FIF.DELETE, "删除")
        self.delete_region_button.setProperty("dangerAction", True)
        self.delete_region_button.clicked.connect(self.delete_selected_region)
        region_actions.addWidget(self.rename_region_button)
        region_actions.addWidget(self.delete_region_button)
        box.addLayout(region_actions)
        row = QHBoxLayout()
        row.setSpacing(6)
        save = PushButton(FIF.SAVE, "保存模板")
        save.clicked.connect(self.save_template)
        clear = PushButton(FIF.BROOM, "清空")
        clear.clicked.connect(self.clear_regions)
        row.addWidget(save)
        row.addWidget(clear)
        box.addLayout(row)
        rl.addWidget(self.crop_tools)

        self.shooting_options = FoldSection("定时拍摄", icon=FIF.STOP_WATCH)
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
        self.interval_button = PushButton(FIF.STOP_WATCH, "定时采集")
        self.interval_button.clicked.connect(self.toggle_interval)
        box.addWidget(self.interval_button)
        rl.addWidget(self.shooting_options)

        self.experiment_options = FoldSection("实验信息", icon=FIF.TAG)
        box = self.experiment_options.content_layout
        self.tags = LineEdit()
        self.tags.setPlaceholderText("标签，逗号分隔")
        box.addWidget(self.tags)
        self.note = TextEdit()
        self.note.setPlaceholderText("实验条件或备注")
        self.note.setFixedHeight(84)
        box.addWidget(self.note)
        self.custom = MetadataEditor()
        box.addWidget(self.custom)
        rl.addWidget(self.experiment_options)
        rl.addStretch()

        scroll.setWidget(right)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([860, 336])

        self.camera.preview.connect(self.canvas.set_image)
        self.camera.status.connect(self.camera_status)
        self.camera.connected.connect(self.camera_connected)
        self.camera.devices_changed.connect(self._populate_devices)
        self.camera.mvs.settings_changed.connect(self._sync_mvs_settings)
        self.camera.settings_frame_ready.connect(self.update_capture_controls)
        self.camera.recording_changed.connect(self.update_capture_controls)
        self.camera.recording_duration.connect(
            lambda duration: self.recording_time.setText(video_duration(duration))
        )
        self.camera.video_finished.connect(self._video_finished)
        self.camera.video_error.connect(self._video_failed)
        self.canvas.regions_changed.connect(self.regions_changed)
        self.canvas.selection_changed.connect(self._canvas_selection_changed)
        self.canvas.dimensions_changed.connect(self.dimensions_changed)
        self.canvas.stop_interval_requested.connect(self.stop_interval)

        self._sync_exposure_slider()
        self._sync_gain_slider()
        self._update_region_buttons()
        self.refresh_devices()
        self.refresh()
        self.camera_connected(False)

    def _auto_increment_changed(self, *_):
        self.app.preferences.setValue("capture/sample_auto_increment", self.auto_increment.isChecked())

    def _open_recent_photo(self, index):
        row = index.data(ROLE)
        if not row:
            return
        photos = list(self.recent_model.rows)
        pos = next((i for i, item in enumerate(photos) if item["id"] == row["id"]), 0)
        self.app.show_photo(row, photos=photos, index=pos)

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
        self.batch_info.setText("" if self.batch_id() else "点击右侧「＋」新建采集批次")
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
        self.auto_increment.setVisible(not self.video_mode)
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
        ready = self.camera.active and self.camera.capture_settings_ready
        self.options_scroll.setEnabled(not busy and not saving_photo)
        self.mode_combo.setEnabled(not busy and not saving_photo)
        self.sample.setEnabled(not busy)
        self.auto_increment.setEnabled(not busy)
        self.browse_videos.setEnabled(not busy)
        self.disconnect_button.setEnabled(self.camera.active and not busy and not saving_photo)
        self.recording_time.setVisible(busy)
        self.app.navigationInterface.setEnabled(not busy and not self.app.runner.busy)
        self.shoot.setEnabled(
            (ready or busy) and not self.app.runner.busy and not self.camera.recording_stopping
        )
        if self.camera.recording_stopping:
            self.shoot.setText("正在保存视频…")
        elif busy:
            self.shoot.setText("停止并保存  Space")
        elif saving_photo:
            self.shoot.setText("保存中…")
        elif self.countdown_timer.isActive():
            self.shoot.setText(f"{self.countdown_remaining} 秒 · 点击取消")
        elif self.video_mode:
            self.shoot.setText("开始录制  Space")
        else:
            self.shoot.setText("拍照  Space")
        self.shoot.setIcon(FIF.VIDEO if self.video_mode else FIF.CAMERA)
        self.interval_button.setEnabled(ready and not self.video_mode)
        self._update_mvs_controls()

    def open_videos(self):
        self.app.open_library(self.dataset_id(), "video", self.batch_id())

    def request_capture(self):
        if not self.camera.capture_settings_ready:
            return
        if self.video_mode:
            self.toggle_recording()
        else:
            self.request_photo()

    def toggle_recording(self):
        if self.camera.video_busy:
            self.camera.stop_recording()
            return
        if self.app.runner.busy or not self.camera.capture_settings_ready:
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
                    started_at=display_now().isoformat(),
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
        values = dict(name=data["name"] if data else display_now().strftime("采集 %m-%d %H:%M"))
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
        self._populate_devices()
        self.camera.mvs.refresh()

    def _dismiss_mvs_warning(self):
        self._mvs_warning_dismissed = True
        self.app.preferences.setValue("capture/mvs_warning_dismissed", True)
        self.mvs_warning.hide()

    def _populate_devices(self):
        current = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        devices = self.camera.devices()
        for device in devices:
            self.device_combo.addItem(device.description(), userData=bytes(device.id()).hex())
        i = next((i for i, d in enumerate(devices) if bytes(d.id()).hex() == current), 0)
        self.device_combo.setCurrentIndex(i if devices else -1)
        self.device_combo.blockSignals(False)
        error = self.camera.mvs.discovery_error
        if not error and self.camera.mvs.sdk_ready and self._mvs_warning_dismissed:
            self._mvs_warning_dismissed = False
            self.app.preferences.setValue("capture/mvs_warning_dismissed", False)
        self.device_combo.setToolTip(error)
        self.mvs_warning_label.setText(error)
        self.mvs_warning.setVisible(bool(error) and not self._mvs_warning_dismissed)
        self.refresh_formats()

    def refresh_formats(self, *_):
        self.format_combo.clear()
        for label in self.camera.format_labels(self.device_combo.currentIndex()):
            self.format_combo.addItem(label)
        self._update_camera_summary()

    def _format_selected(self, *_):
        self._update_camera_summary()
        if (
            self.camera.active
            and not self.camera.video_busy
            and not self.running
            and self.device_combo.currentIndex() == self._active_device_index
        ):
            self.connect_camera()

    def connect_camera(self):
        if self.camera.video_busy:
            return
        self.stop_interval()
        self._active_device_index = self.device_combo.currentIndex()
        self.camera.open(self._active_device_index, self.format_combo.currentIndex())

    def disconnect_camera(self):
        self.stop_interval()
        self.camera.stop()
        self.canvas.set_image(QImage())
        self._applying_settings = False
        self._active_device_index = -1
        self.stream_status.set_status("已断开设备", "neutral", dot=True)

    def camera_status(self, message):
        tone = "warning" if any(k in message for k in ("正在", "等待", "重连")) else "neutral"
        if "已连接" in message:
            tone = "success"
        elif any(k in message for k in ("失败", "断开", "错误", "无法", "未找到", "权限")):
            tone = "danger" if "断开" not in message else "neutral"
        self.stream_status.set_status(message, tone, dot=True)

    def _update_camera_summary(self):
        if not self.camera.active:
            self.camera_options.set_summary("")
            return
        parts = []
        if self.camera.mvs.active and self.camera.mvs.settings.candling:
            parts.append("照蛋")
        if self.format_combo.currentText():
            parts.append(self.format_combo.currentText().split("·")[0].strip())
        self.camera_options.set_summary(" · ".join(parts))

    def _update_candling_hint(self):
        if not self.camera.mvs.active or self.camera.mvs.device is None:
            return
        preset = self.app.preferences.value(self._candling_key())
        if (
            isinstance(preset, dict)
            and all(
                isinstance(preset.get(key), (int, float)) and isfinite(preset[key])
                for key in ("exposure_us", "gain_db")
            )
        ):
            self.candling_preset_hint.setText(
                f"已保存预设 · {preset['exposure_us'] / 1000:.1f} ms / {preset['gain_db']:.1f} dB"
            )
        else:
            self.candling_preset_hint.setText("尚未保存本机预设")

    def camera_connected(self, active):
        self.shoot.setEnabled(active and not self.app.runner.busy)
        self.interval_button.setEnabled(active)
        camera = self.camera.camera
        if not active:
            self.stop_interval()
            self._applying_settings = False
        support = camera.supportedFeatures() if active and camera else QCamera.Feature(0)
        if active and self.camera.mvs.active:
            self._native_exposure = (self.exposure_auto.isChecked(), self.exposure.value())
            self.exposure.setKeyboardTracking(False)
            with QSignalBlocker(self.exposure):
                self.exposure.setDecimals(3)
            self._sync_mvs_settings()
        else:
            if self._native_exposure is not None:
                auto, value = self._native_exposure
                self._native_exposure = None
                with QSignalBlocker(self.exposure_auto), QSignalBlocker(self.exposure):
                    self.exposure_auto.setChecked(auto)
                    self.exposure.setKeyboardTracking(True)
                    self.exposure.setDecimals(2)
                    self.exposure.setRange(0.01, 1000)
                    self.exposure.setValue(value)
                self._sync_exposure_slider()
            manual_exp = bool(support & QCamera.Feature.ManualExposureTime)
            self.exposure_auto.setEnabled(manual_exp)
            self.exposure.setEnabled(manual_exp and not self.exposure_auto.isChecked())
            self.exposure_slider.setEnabled(self.exposure.isEnabled())
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
            self.stream_status.set_status("已连接 · " + self.camera.device_name, "success", dot=True)
        if active and camera:
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
        self._update_camera_summary()
        self.update_capture_controls()

    def _sync_mvs_settings(self):
        stream = self.camera.mvs
        if not stream.active:
            return
        if stream.settings_pending:
            self._applying_settings = True
            self.camera_status("正在应用相机参数并等待新画面…")
            self.update_capture_controls()
            return
        settings = stream.settings
        with QSignalBlocker(self.exposure_auto), QSignalBlocker(self.candling_mode):
            self.exposure_auto.setChecked(False)
            self.candling_mode.setChecked(settings.candling)
        for widget, control, scale in (
            (self.exposure, settings.exposure, 1000),
            (self.gain, settings.gain, 1),
        ):
            if control.value is not None:
                with QSignalBlocker(widget):
                    widget.setRange(control.minimum / scale, control.maximum / scale)
                    widget.setValue(control.value / scale)
        self._sync_exposure_slider()
        self._sync_gain_slider()
        self.exposure_auto.setEnabled(False)
        if self._applying_settings:
            self._applying_settings = False
            self.stream_status.set_status("已连接 · " + self.camera.device_name, "success", dot=True)
        self._update_candling_hint()
        self._update_camera_summary()
        self.update_capture_controls()

    def _update_mvs_controls(self):
        stream = self.camera.mvs
        settings = stream.settings
        has_gain = stream.active and settings.gain.value is not None
        self.gain_label.setVisible(has_gain)
        self.gain.setVisible(has_gain)
        self.gain_slider.setVisible(has_gain)
        self.candling_card.setVisible(stream.active)
        self.candling_mode.setVisible(stream.active)
        self.save_candling.setVisible(stream.active)
        self.candling_badge.setVisible(stream.active and settings.candling)
        for widget in self._uvc_controls:
            widget.setVisible(not stream.active)
        if not stream.active:
            self.exposure_slider.setEnabled(self.exposure.isEnabled())
            return
        editable = self._mvs_settings_editable()
        self.exposure.setEnabled(editable and settings.exposure.writable)
        self.exposure_slider.setEnabled(editable and settings.exposure.writable)
        self.gain.setEnabled(editable and settings.gain.writable)
        self.gain_slider.setEnabled(editable and settings.gain.writable)
        self.candling_mode.setEnabled(editable and (settings.supports_candling or settings.candling))
        self.save_candling.setEnabled(editable and settings.candling and settings.supports_candling)
        self.candling_mode.setToolTip(
            "开启时应用这台相机保存的曝光和增益"
            if settings.supports_candling
            else "需要相机支持手动曝光和手动增益"
        )

    def _exposure_to_slider(self, value):
        low = max(self.exposure.minimum(), 0.01)
        high = max(self.exposure.maximum(), low * 1.01)
        clamped = max(low, min(high, value))
        ratio = math.log(clamped / low) / math.log(high / low)
        return round(ratio * 1000)

    def _slider_to_exposure(self, pos):
        low = max(self.exposure.minimum(), 0.01)
        high = max(self.exposure.maximum(), low * 1.01)
        value = low * ((high / low) ** (pos / 1000))
        return round(value, self.exposure.decimals())

    def _sync_exposure_slider(self):
        with QSignalBlocker(self.exposure_slider):
            self.exposure_slider.setValue(self._exposure_to_slider(self.exposure.value()))

    def _exposure_slider_moved(self, pos):
        with QSignalBlocker(self.exposure):
            self.exposure.setValue(self._slider_to_exposure(pos))

    def _exposure_slider_changed(self, pos):
        if not self.exposure_slider.isSliderDown():
            with QSignalBlocker(self.exposure):
                self.exposure.setValue(self._slider_to_exposure(pos))
            self._exposure_commit_timer.start()

    def _commit_exposure_slider(self):
        self._exposure_commit_timer.stop()
        self.set_exposure()

    def _sync_gain_slider(self):
        low, high = self.gain.minimum(), self.gain.maximum()
        steps = max(1, round((high - low) * 10))
        pos = round((self.gain.value() - low) * 10)
        with QSignalBlocker(self.gain_slider):
            self.gain_slider.setRange(0, steps)
            self.gain_slider.setValue(max(0, min(steps, pos)))

    def _gain_slider_moved(self, pos):
        value = round(self.gain.minimum() + pos / 10, 2)
        with QSignalBlocker(self.gain):
            self.gain.setValue(value)

    def _gain_slider_changed(self, pos):
        if not self.gain_slider.isSliderDown():
            value = round(self.gain.minimum() + pos / 10, 2)
            with QSignalBlocker(self.gain):
                self.gain.setValue(value)
            self._gain_commit_timer.start()

    def _commit_gain_slider(self):
        self._gain_commit_timer.stop()
        self.set_gain(self.gain.value())

    def _candling_key(self):
        return f"capture/candling/{self.camera.mvs.device.id().hex()}"

    def _mvs_settings_editable(self):
        return (
            self.camera.mvs.active
            and self.camera.capture_settings_ready
            and not self.running
            and not self.camera.video_busy
            and not self.camera.saving_photo
        )

    def set_candling_mode(self, enabled):
        if not self._mvs_settings_editable():
            return
        preset = self.app.preferences.value(self._candling_key()) if enabled else None
        if isinstance(preset, dict) and all(
            isinstance(preset.get(key), (int, float)) and isfinite(preset[key])
            for key in ("exposure_us", "gain_db")
        ):
            change = SettingsChange(
                exposure_us=preset["exposure_us"], gain_db=preset["gain_db"], candling=True
            )
        else:
            change = SettingsChange(candling=enabled)
        self.camera.mvs.adjust(change)

    def save_candling_preset(self):
        settings = self.camera.mvs.settings
        if not (self._mvs_settings_editable() and settings.candling and settings.supports_candling):
            return
        preferences = self.app.preferences
        preferences.setValue(
            self._candling_key(), dict(exposure_us=settings.exposure.value, gain_db=settings.gain.value)
        )
        preferences.sync()
        if preferences.status() == QSettings.Status.NoError:
            self._update_candling_hint()
            self.app.notify("已保存照蛋预设")
        else:
            self.app.notify("无法将照蛋预设写入应用设置", error=True)

    def set_exposure(self, *_):
        self._sync_exposure_slider()
        if self.camera.mvs.active:
            if self._mvs_settings_editable():
                self.camera.mvs.adjust(SettingsChange(exposure_us=self.exposure.value() * 1000))
            return
        camera = self.camera.camera
        if camera and camera.supportedFeatures() & QCamera.Feature.ManualExposureTime:
            auto = self.exposure_auto.isChecked()
            camera.setExposureMode(
                QCamera.ExposureMode.ExposureAuto if auto else QCamera.ExposureMode.ExposureManual
            )
            self.exposure.setEnabled(not auto)
            self.exposure_slider.setEnabled(not auto)
            if auto:
                camera.setAutoExposureTime()
            else:
                camera.setManualExposureTime(self.exposure.value() / 1000)

    def set_gain(self, value):
        self._sync_gain_slider()
        if self._mvs_settings_editable():
            self.camera.mvs.adjust(SettingsChange(gain_db=value))

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
        if width:
            self.image_size.set_status(f"{width} × {height}", "info", dot=False)
        else:
            self.image_size.set_status("— × —", "neutral", dot=False)
        if self.template and (width, height) != (self.template["width"], self.template["height"]):
            self.template = None
            self.template_status.setText("分辨率已变化，请重新选择模板或标定热区")
            self.template_combo.blockSignals(True)
            self.template_combo.setCurrentIndex(0)
            self.template_combo.blockSignals(False)

    def _canvas_selection_changed(self, index):
        with QSignalBlocker(self.region_list):
            self.region_list.setCurrentRow(index)
        self._update_region_buttons()

    def _region_row_changed(self, row):
        self.canvas.select_region(row)
        self._update_region_buttons()

    def _update_region_buttons(self):
        has_selection = self.region_list.currentRow() >= 0 or self.canvas.selected >= 0
        self.rename_region_button.setEnabled(has_selection)
        self.delete_region_button.setEnabled(has_selection)

    def regions_changed(self, regions):
        if self.template and regions != self.template["regions"]:
            self.template = None
        with QSignalBlocker(self.region_list):
            self.region_list.clear()
            self.region_list.addItems([f"{r['name']}   {r['w']} × {r['h']}" for r in regions])
            if 0 <= self.canvas.selected < len(regions):
                self.region_list.setCurrentRow(self.canvas.selected)
        self.template_status.setText(
            f"{len(regions)} 个热区 · 原图与裁剪图一并保存" if regions else "未设置热区"
        )
        self.crop_tools.set_summary(f"{len(regions)} 个" if regions else "")
        self._update_region_buttons()

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

    def delete_selected_region(self):
        index = self.region_list.currentRow()
        if index < 0:
            index = self.canvas.selected
        if index < 0 or index >= len(self.canvas.regions):
            return
        regions = [dict(r) for i, r in enumerate(self.canvas.regions) if i != index]
        self.canvas.set_regions(regions)

    def clear_regions(self):
        if not self.canvas.regions:
            return
        if confirm(self, "清空所有热区？", f"将移除当前画布上的 {len(self.canvas.regions)} 个热区。", True):
            self.canvas.set_regions([])

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
        self.canvas.reset_view()

    def request_photo(self):
        if self.video_mode or self.camera.video_busy:
            return
        if self.countdown_timer.isActive():
            self.countdown_timer.stop()
            self.countdown_remaining = 0
            self.canvas.set_countdown(0)
            self.shoot.setText("拍照  Space")
            return
        if self.app.runner.busy:
            return
        seconds = self.countdown.value()
        if seconds:
            self.countdown_remaining = seconds
            self.canvas.set_countdown(seconds)
            self.shoot.setText(f"{seconds} 秒 · 点击取消")
            self.countdown_timer.start()
        else:
            self.take_photo()

    def _countdown_tick(self):
        self.countdown_remaining -= 1
        if self.countdown_remaining <= 0:
            self.countdown_timer.stop()
            self.canvas.set_countdown(0)
            self.shoot.setText("拍照  Space")
            self.take_photo()
        else:
            self.canvas.set_countdown(self.countdown_remaining)
            self.shoot.setText(f"{self.countdown_remaining} 秒 · 点击取消")

    def _update_interval_hud(self):
        if not self.running:
            self.canvas.set_interval_status(False)
            self.shooting_options.set_summary("")
            return
        remaining_ms = max(0, self.shot_timer.remainingTime())
        remaining_s = max(1, math.ceil(remaining_ms / 1000))
        self.canvas.set_interval_status(True, remaining_s, self.interval_shots)
        self.shooting_options.set_summary(f"运行中 · {remaining_s}s")

    def toggle_interval(self):
        if self.running:
            self.stop_interval()
        elif not self.camera.capture_settings_ready:
            return
        elif self.batch_id():
            self.running = True
            self.interval_shots = 0
            self.interval_button.setText("停止采集")
            self.shot_timer.start(self.interval.value() * 1000)
            self.interval_hud_timer.start()
            self._update_interval_hud()
            self.update_capture_controls()
            self.take_photo()
        else:
            self.app.notify("请先选择采集批次", error=True)

    def stop_interval(self):
        self.running = False
        self.shot_timer.stop()
        self.countdown_timer.stop()
        self.interval_hud_timer.stop()
        self.countdown_remaining = 0
        if hasattr(self, "canvas"):
            self.canvas.set_countdown(0)
            self.canvas.set_interval_status(False)
        if hasattr(self, "shooting_options"):
            self.shooting_options.set_summary("")
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
            self.canvas.trigger_flash()
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
        if self.running:
            self.interval_shots += 1
            self._update_interval_hud()
        if self.auto_increment.isChecked():
            current = self.sample.text().strip()
            updated = increment_sample_id(current)
            if updated != current:
                self.sample.setText(updated)
        self.refresh_recent()
        self.app.home.refresh()
        self.app.library.refresh()
        self.app.feedback.play()
        self.app.notify("采集成功 · 原图与裁剪图已保存")
