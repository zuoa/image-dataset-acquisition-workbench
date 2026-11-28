import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    SwitchButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from .. import __version__, mvs
from ..timeutils import display_now, format_datetime
from ..updater import RELEASES_PAGE_URL
from .common import IconBadge, confirm, heading, keycap_row, muted, panel
from .theme import BLUE, BORDER, INK, MUTED, RADIUS_CARD, SURFACE, SURFACE_HOVER, SoftCard, StatusPill


class TrashPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("trash")
        self.items = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(14)

        row = QHBoxLayout()
        row.addWidget(
            heading("回收站", "误删的记录支持多选批量恢复。清空后将永久删除照片、裁剪图与视频。", "RECOVERY"), 1
        )
        self.clear_button = PushButton(FIF.DELETE, "清空回收站")
        self.clear_button.setProperty("dangerAction", True)
        self.clear_button.clicked.connect(self.empty)
        row.addWidget(self.clear_button)
        layout.addLayout(row)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setIconSize(QSize(16, 16))
        # Native item views inherit the system palette independently of Fluent's light theme.
        self.list.setStyleSheet(
            f"""
            QListWidget {{
                background: {SURFACE};
                color: {INK};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CARD}px;
                padding: 8px;
                outline: none;
            }}
            QListWidget::item {{ padding: 12px 14px; border-radius: 10px; margin: 2px 0; }}
            QListWidget::item:hover {{ background: {SURFACE_HOVER}; }}
            QListWidget::item:selected {{ background: {BLUE}; color: #FDFCFA; }}
            """
        )
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.list.currentRowChanged.connect(self._update_buttons)
        layout.addWidget(self.list, 1)

        self.empty_card = SoftCard()
        empty_layout = QVBoxLayout(self.empty_card)
        empty_layout.setContentsMargins(28, 42, 28, 42)
        empty_layout.setSpacing(10)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(
            IconBadge(FIF.DELETE, MUTED, size=28, parent=self.empty_card),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        empty_title = SubtitleLabel("回收站为空")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_hint = muted("删除的数据集、批次、图片或视频会暂时保留在这里，可随时恢复。")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_hint)
        self.empty_card.hide()
        layout.addWidget(self.empty_card, 1)

        footer = QHBoxLayout()
        self.summary = muted("")
        footer.addWidget(self.summary, 1)
        self.restore_button = PrimaryPushButton(FIF.HISTORY, "恢复选中记录")
        self.restore_button.clicked.connect(self.restore)
        footer.addWidget(self.restore_button)
        layout.addLayout(footer)
        self.refresh()

    def _selected_rows(self):
        rows = sorted({index.row() for index in self.list.selectedIndexes()})
        if not rows and 0 <= self.list.currentRow() < len(self.items):
            rows = [self.list.currentRow()]
        return [r for r in rows if 0 <= r < len(self.items)]

    def _update_buttons(self, *_):
        selected = self._selected_rows()
        self.clear_button.setEnabled(bool(self.items))
        self.restore_button.setEnabled(bool(selected))
        if len(selected) > 1:
            self.restore_button.setText(f"恢复选中的 {len(selected)} 条记录")
        else:
            self.restore_button.setText("恢复选中记录")

    def refresh(self):
        self.items = self.app.workspace.trash_items()
        self.list.clear()
        labels = {"dataset": "数据集", "batch": "批次", "photo": "图片", "video": "视频"}
        icons = {"dataset": FIF.FOLDER, "batch": FIF.TILES, "photo": FIF.PHOTO, "video": FIF.VIDEO}
        for item in self.items:
            kind = item["kind"]
            row_item = QListWidgetItem(
                icons[kind].icon(),
                f"  {labels[kind]}  ·  {item['name'] or item['id'][:12]}   ·   {format_datetime(item['deleted_at'])}",
            )
            self.list.addItem(row_item)
        has_items = bool(self.items)
        self.list.setVisible(has_items)
        self.empty_card.setVisible(not has_items)
        self.summary.setText(
            f"{len(self.items)} 条删除记录 · 按 Ctrl / ⌘ 或 Shift 可多选恢复" if has_items else "回收站为空"
        )
        self._update_buttons()

    def restore(self):
        rows = self._selected_rows()
        if not rows:
            return
        for row in rows:
            item = self.items[row]
            self.app.workspace.restore(item["kind"], item["id"])
        self.app.refresh()
        if len(rows) > 1:
            self.app.notify(f"已恢复 {len(rows)} 条记录；必要的父级数据集与批次一并恢复")
        else:
            self.app.notify("记录已恢复；必要的父级数据集与批次一并恢复")

    def empty(self):
        if self.items and confirm(
            self, "永久清空回收站？", "将永久删除所有已删除记录及其图片、视频文件，此操作无法撤销。", True
        ):
            self.app.start_task(
                "正在清空回收站…",
                lambda cancel, progress: self.app.workspace.empty_trash(),
                lambda _: self.app.refresh(),
            )


class SettingsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._mvs_recheck_notify = False
        self.setObjectName("settings")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(14)
        layout.addWidget(heading("工作区与设置", "数据留在本机，采集无需连接网络。", "PREFERENCES"))

        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 6, 0, 8)
        outer_layout.addStretch(1)

        content = QWidget()
        content.setMaximumWidth(860)
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(14)

        card, cl = panel("采集完成提示", "照片或视频完整保存后播放提示音。", icon=FIF.VOLUME)
        row = QHBoxLayout()
        self.capture_sound = SwitchButton("采集完成音效")
        self.capture_sound.setOnText("已开启")
        self.capture_sound.setOffText("已关闭")
        self.capture_sound.setChecked(app.feedback.enabled)
        self.capture_sound.checkedChanged.connect(app.feedback.set_enabled)
        row.addWidget(self.capture_sound)
        row.addStretch()
        preview = PushButton(FIF.VOLUME, "试听")
        preview.clicked.connect(app.feedback.preview)
        row.addWidget(preview)
        cl.addLayout(row)
        box.addWidget(card)

        card, cl = panel(
            "海康 MVS SDK",
            "连接海康 MV-CS016-10UM 工业相机时需要；仅使用普通 USB 摄像头无需安装。",
            icon=FIF.CAMERA,
        )
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.mvs_status = StatusPill("正在检测…", "neutral", dot=True)
        status_row.addWidget(self.mvs_status)
        self.mvs_detail = muted("")
        status_row.addWidget(self.mvs_detail, 1)
        cl.addLayout(status_row)
        self.mvs_guide = muted(mvs.platform_guide())
        cl.addWidget(self.mvs_guide)
        self.mvs_path_label = BodyLabel("")
        self.mvs_path_label.setWordWrap(True)
        self.mvs_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(self.mvs_path_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.mvs_download_button = PrimaryPushButton(FIF.DOWNLOAD, "前往海康官网下载")
        self.mvs_download_button.clicked.connect(lambda: app.open_url(mvs.DOWNLOAD_URL))
        self.mvs_select_button = PushButton(FIF.FOLDER, "选择本地 SDK…")
        self.mvs_select_button.clicked.connect(self.choose_mvs_library)
        self.mvs_reset_button = PushButton(FIF.HISTORY, "恢复默认")
        self.mvs_reset_button.clicked.connect(self.reset_mvs_library)
        self.mvs_recheck_button = PushButton(FIF.SYNC, "重新检测")
        self.mvs_recheck_button.clicked.connect(self.recheck_mvs)
        row.addWidget(self.mvs_download_button)
        row.addWidget(self.mvs_select_button)
        row.addWidget(self.mvs_reset_button)
        row.addWidget(self.mvs_recheck_button)
        row.addStretch()
        cl.addLayout(row)
        box.addWidget(card)

        card, cl = panel("本地工作区", icon=FIF.FOLDER)
        self.path_label = BodyLabel(str(app.workspace.root))
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(self.path_label)
        self.disk_progress = ProgressBar()
        self.disk_progress.setRange(0, 100)
        cl.addWidget(self.disk_progress)
        self.space = muted("")
        cl.addWidget(self.space)
        row = QHBoxLayout()
        row.setSpacing(8)
        reveal = PushButton(FIF.FOLDER, "打开工作区")
        reveal.clicked.connect(lambda: app.open_path(app.workspace.root))
        change = PushButton(FIF.EDIT, "更换工作区")
        change.clicked.connect(self.change_workspace)
        row.addWidget(reveal)
        row.addWidget(change)
        row.addStretch()
        cl.addLayout(row)
        self.pending = muted("工作区采用单实例写入；更换位置将在下次启动时生效。")
        cl.addWidget(self.pending)
        box.addWidget(card)

        card, cl = panel("备份与恢复", "完整工作区备份包含所有数据集、视频、回收站和历史裁剪版本。", icon=FIF.SAVE)
        row = QHBoxLayout()
        row.setSpacing(8)
        backup = PrimaryPushButton(FIF.SAVE, "备份工作区")
        backup.clicked.connect(self.backup)
        restore = PushButton(FIF.HISTORY, "恢复到新工作区")
        restore.clicked.connect(self.restore)
        row.addWidget(backup)
        row.addWidget(restore)
        row.addStretch()
        cl.addLayout(row)
        cl.addWidget(muted("分享单个数据集，请在素材库使用「导出 ZIP」；完整工作区备份用于本机迁移和恢复。"))
        box.addWidget(card)

        card, cl = panel("操作与诊断", "常用交互与快捷键一览。", icon=FIF.COMMAND_PROMPT)
        shortcuts = QGridLayout()
        shortcuts.setHorizontalSpacing(20)
        shortcuts.setVerticalSpacing(8)
        cheats = [
            ("Space", "拍照 / 开始或停止录制"),
            ("滚轮 / 双击", "缩放画布 / 适应窗口"),
            ("Alt + 拖动", "任意模式平移预览画布"),
            ("Delete", "删除选中热区或素材"),
            ("← / →", "详情窗口切换上一张 / 下一张"),
            ("Enter / 右键", "打开素材 / 打开快捷菜单"),
            ("Ctrl / ⌘ / Shift", "素材库与回收站多选"),
            ("定时采集", "保存较慢时自动跳过，避免积压"),
        ]
        for idx, (keys, desc) in enumerate(cheats):
            shortcuts.addWidget(keycap_row(keys, desc), idx // 2, idx % 2)
        cl.addLayout(shortcuts)
        logs = PushButton(FIF.DOCUMENT, "打开日志文件夹")
        logs.clicked.connect(lambda: app.open_path(app.log_dir))
        cl.addWidget(logs, alignment=Qt.AlignmentFlag.AlignLeft)
        box.addWidget(card)

        card, cl = panel("关于 Vannus", "通过 GitHub Releases 检查并安装新版本。", icon=FIF.INFO)
        version_row = QHBoxLayout()
        version_row.setSpacing(10)
        self.version_label = SubtitleLabel(f"版本 {__version__}")
        version_row.addWidget(self.version_label)
        self.update_status = StatusPill("未检查", "neutral", dot=True)
        version_row.addWidget(self.update_status)
        version_row.addStretch()
        cl.addLayout(version_row)
        self.update_detail = muted("")
        cl.addWidget(self.update_detail)
        auto_row = QHBoxLayout()
        self.auto_check_updates = SwitchButton("启动时自动检查更新")
        self.auto_check_updates.setOnText("已开启")
        self.auto_check_updates.setOffText("已关闭")
        self.auto_check_updates.setChecked(app.updater.auto_check)
        self.auto_check_updates.checkedChanged.connect(app.updater.set_auto_check)
        auto_row.addWidget(self.auto_check_updates)
        auto_row.addStretch()
        cl.addLayout(auto_row)
        update_actions = QHBoxLayout()
        update_actions.setSpacing(8)
        self.check_update_button = PushButton(FIF.SYNC, "检查更新")
        self.check_update_button.clicked.connect(lambda: app.check_for_updates(manual=True))
        self.install_update_button = PrimaryPushButton(FIF.DOWNLOAD, "查看并更新")
        self.install_update_button.clicked.connect(app.show_update_dialog)
        self.install_update_button.hide()
        self.releases_button = PushButton(FIF.GLOBE, "打开发布页")
        self.releases_button.clicked.connect(lambda: app.open_url(RELEASES_PAGE_URL))
        update_actions.addWidget(self.check_update_button)
        update_actions.addWidget(self.install_update_button)
        update_actions.addWidget(self.releases_button)
        update_actions.addStretch()
        cl.addLayout(update_actions)
        cl.addWidget(muted("Power By AJ"))
        box.addWidget(card)
        box.addStretch()

        outer_layout.addWidget(content, 10)
        outer_layout.addStretch(1)
        scroll.setWidget(outer)
        layout.addWidget(scroll, 1)
        app.capture.camera.devices_changed.connect(self._update_mvs_status)
        app.updater.state_changed.connect(self._update_version_status)
        self.refresh()

    def refresh(self):
        usage = shutil.disk_usage(self.app.workspace.root)
        used_pct = round(usage.used / max(usage.total, 1) * 100)
        self.disk_progress.setValue(used_pct)
        self.space.setText(
            f"剩余空间 {usage.free / 1024**3:.1f} GB  /  总容量 {usage.total / 1024**3:.1f} GB（已用 {used_pct}%）"
        )
        self._update_mvs_status()
        self._update_version_status()

    def _update_version_status(self):
        updater = self.app.updater
        self.auto_check_updates.blockSignals(True)
        self.auto_check_updates.setChecked(updater.auto_check)
        self.auto_check_updates.blockSignals(False)
        self.check_update_button.setEnabled(not updater.checking and not updater.downloading)

        result = updater.last_result
        if updater.checking:
            self.update_status.set_status("正在检查…", "info", dot=True)
            self.update_detail.setText("正在连接 GitHub Releases 获取最新版本信息…")
            self.install_update_button.hide()
            return
        if updater.downloading:
            self.update_status.set_status("正在更新", "info", dot=True)
            self.update_detail.setText("正在下载并校验安装包…")
            self.install_update_button.hide()
            return
        if result is not None and result.update_available and result.latest_release is not None:
            latest = result.latest_release.version.display
            ignored = updater.is_version_ignored(result.latest_release)
            self.update_status.set_status(
                f"可更新 v{latest}" if not ignored else f"已忽略 v{latest}",
                "info" if not ignored else "neutral",
                dot=True,
            )
            detail = f"发现新版本 v{latest}。"
            if result.fallback_reason:
                detail = f"{detail} {result.fallback_reason}"
            self.update_detail.setText(detail)
            self.install_update_button.setText("查看并更新" if result.can_auto_install else "查看新版本")
            self.install_update_button.show()
            return
        if result is not None:
            self.update_status.set_status("已是最新", "success", dot=True)
            self.update_detail.setText(f"当前已是最新版本 v{result.current_version.display}。")
            self.install_update_button.hide()
            return
        if updater.last_error:
            self.update_status.set_status("检查失败", "warning", dot=True)
            self.update_detail.setText(updater.last_error)
            self.install_update_button.hide()
            return

        self.update_status.set_status("未检查", "neutral", dot=True)
        if updater.install_target.kind == "source":
            self.update_detail.setText("当前为源码运行环境；检查到新版本后可前往 GitHub 发布页查看。")
        else:
            self.update_detail.setText("支持通过 GitHub Releases 自动下载、校验并重启更新。")
        self.install_update_button.hide()

    def _update_mvs_status(self):
        path, source = mvs.library_location()
        if source == "env":
            self.mvs_path_label.setText(f"环境变量 VANNUS_MVS_LIBRARY：{path}")
        elif source == "custom":
            self.mvs_path_label.setText(f"自定义 SDK 路径：{path}")
        elif source == "default":
            self.mvs_path_label.setText(f"默认 SDK 路径：{path}")
        else:
            self.mvs_path_label.setText("当前系统暂未提供海康 MVS SDK 支持")

        stream = self.app.capture.camera.mvs
        env_override = source == "env"
        unsupported = source == "unsupported"
        self.mvs_select_button.setEnabled(not env_override and not unsupported)
        self.mvs_reset_button.setEnabled(bool(mvs.get_custom_library_path()) and not env_override)
        self.mvs_recheck_button.setEnabled(not stream._discovering and not unsupported)

        if stream._discovering or stream.sdk_ready is None:
            self.mvs_status.set_status("正在检测…", "neutral", dot=True)
            self.mvs_detail.setText("正在检查本机 MVS SDK 与海康设备…")
        elif stream.sdk_ready:
            self.mvs_status.set_status("已就绪", "success", dot=True)
            count = len(stream.devices)
            self.mvs_detail.setText(
                f"SDK 加载成功 · 已发现 {count} 台海康相机" if count else "SDK 加载成功 · 当前未连接海康相机"
            )
        else:
            self.mvs_status.set_status("未加载", "warning", dot=True)
            self.mvs_detail.setText(stream.discovery_error or "无法加载海康 MVS SDK")

        if self._mvs_recheck_notify and not stream._discovering and stream.sdk_ready is not None:
            self._mvs_recheck_notify = False
            if stream.sdk_ready:
                self.app.notify("海康 MVS SDK 已就绪，可在采集页连接海康相机")
            else:
                self.app.notify(stream.discovery_error or "仍无法加载海康 MVS SDK", error=True)

    def choose_mvs_library(self):
        camera = self.app.capture.camera
        if camera.video_busy or camera.mvs.active:
            self.app.notify("请先断开当前海康相机后再更改 SDK 路径", error=True)
            return
        if sys.platform == "darwin":
            start_dir = "/Library/Frameworks" if Path("/Library/Frameworks").is_dir() else ""
            caption = "选择海康 MVS SDK 文件（MvCameraControl 或 libMvCameraControl.dylib）"
            file_filter = "所有文件 (*)"
        elif sys.platform == "win32":
            start_dir = ""
            for variable in ("CommonProgramFiles(x86)", "CommonProgramW6432", "CommonProgramFiles"):
                root = os.environ.get(variable)
                if root and (Path(root) / "MVS").is_dir():
                    start_dir = str(Path(root) / "MVS")
                    break
            caption = "选择海康 MvCameraControl.dll"
            file_filter = "动态链接库 (*.dll);;所有文件 (*)"
        else:
            start_dir = ""
            caption = "选择海康 MVS SDK 文件"
            file_filter = "所有文件 (*)"
        selected, _ = QFileDialog.getOpenFileName(self, caption, start_dir, file_filter)
        if not selected:
            return
        normalized = str(mvs.normalize_library_path(selected))
        self.app.preferences.setValue("mvs/library_path", normalized)
        mvs.set_custom_library_path(normalized)
        self.recheck_mvs()

    def reset_mvs_library(self):
        camera = self.app.capture.camera
        if camera.video_busy or camera.mvs.active:
            self.app.notify("请先断开当前海康相机后再恢复默认 SDK 路径", error=True)
            return
        self.app.preferences.remove("mvs/library_path")
        mvs.set_custom_library_path("")
        self.recheck_mvs()

    def recheck_mvs(self):
        self._mvs_recheck_notify = True
        self.app.capture.refresh_devices()
        self._update_mvs_status()

    def change_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "选择新的工作区（下次启动生效）")
        if path:
            self.app.preferences.setValue("workspace", path)
            self.pending.setText(f"下次启动将使用：{path}。当前数据仍保存在上方工作区。")

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "备份工作区", "Vannus-工作区.zip", "ZIP 备份 (*.zip)")
        if path:
            self.app.start_task(
                "正在备份完整工作区…",
                lambda cancel, progress: self.app.workspace.backup(path, cancel=cancel, progress=progress),
                lambda _: self.app.notify("工作区备份已完成"),
            )

    def restore(self):
        archive, _ = QFileDialog.getOpenFileName(self, "选择完整工作区备份", "", "ZIP 备份 (*.zip)")
        if not archive:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择恢复位置（会创建新的工作区目录）")
        if not parent:
            return
        destination = Path(parent) / display_now().strftime("Vannus-恢复-%Y%m%d-%H%M%S")

        def done(_):
            self.app.preferences.setValue("workspace", str(destination))
            self.pending.setText(f"备份已恢复，下次启动将使用：{destination}")
            self.app.notify("工作区已恢复；重新启动后切换，当前工作区保持可用")

        self.app.start_task(
            "正在验证并恢复工作区…",
            lambda cancel, progress: self.app.workspace.restore_backup(
                archive, destination, cancel=cancel, progress=progress
            ),
            done,
        )
