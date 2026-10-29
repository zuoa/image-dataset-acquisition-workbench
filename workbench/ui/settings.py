import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QListWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    SwitchButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from .. import __version__
from .common import confirm, heading, muted, panel
from .theme import BLUE, INK


class TrashPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("trash")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 24)
        row = QHBoxLayout()
        row.addWidget(
            heading("回收站", "误删的记录可以恢复。清空后将永久删除照片、裁剪图与视频。", "RECOVERY"), 1
        )
        clear = PushButton(FIF.DELETE, "清空回收站")
        clear.clicked.connect(self.empty)
        row.addWidget(clear)
        layout.addLayout(row)
        self.list = QListWidget()
        # Native item views inherit the system palette independently of Fluent's light theme.
        self.list.setStyleSheet(
            f"""
            QListWidget {{
                background: white;
                color: {INK};
                border: 1px solid #E0E6EC;
                border-radius: 10px;
                padding: 10px;
                selection-background-color: {BLUE};
                selection-color: white;
            }}
            QListWidget::item {{ padding: 15px; border-radius: 6px; }}
            QListWidget::item:hover {{ background: #F4F7FF; }}
            QListWidget::item:selected {{ background: {BLUE}; color: white; }}
            """
        )
        layout.addWidget(self.list, 1)
        self.summary = muted("")
        layout.addWidget(self.summary)
        restore = PrimaryPushButton("恢复选中记录")
        restore.clicked.connect(self.restore)
        layout.addWidget(restore, alignment=Qt.AlignmentFlag.AlignRight)
        self.refresh()

    def refresh(self):
        self.items = self.app.workspace.trash_items()
        self.list.clear()
        labels = {"dataset": "数据集", "batch": "批次", "photo": "图片", "video": "视频"}
        for item in self.items:
            self.list.addItem(
                f"{labels[item['kind']]}    {item['name'] or item['id'][:12]}    ·    {item['deleted_at'][:19].replace('T', ' ')} UTC"
            )
        self.summary.setText(f"{len(self.items)} 条删除记录" if self.items else "回收站为空")

    def restore(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.items):
            item = self.items[i]
            self.app.workspace.restore(item["kind"], item["id"])
            self.app.refresh()
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
        self.setObjectName("settings")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 24)
        layout.addWidget(heading("工作区与设置", "数据留在本机，采集无需连接网络。", "PREFERENCES"))
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 20, 0, 0)
        box.setSpacing(18)
        card, cl = panel("采集完成提示", "照片或视频完整保存后播放提示音。")
        row = QHBoxLayout()
        self.capture_sound = SwitchButton("采集完成音效")
        self.capture_sound.setOnText("已开启")
        self.capture_sound.setOffText("已关闭")
        self.capture_sound.setChecked(app.feedback.enabled)
        self.capture_sound.checkedChanged.connect(app.feedback.set_enabled)
        row.addWidget(self.capture_sound)
        row.addStretch()
        preview = PushButton("试听")
        preview.clicked.connect(app.feedback.preview)
        row.addWidget(preview)
        cl.addLayout(row)
        box.addWidget(card)
        card, cl = panel("本地工作区")
        self.path_label = BodyLabel(str(app.workspace.root))
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(self.path_label)
        self.space = muted("")
        cl.addWidget(self.space)
        row = QHBoxLayout()
        reveal = PushButton(FIF.FOLDER, "打开工作区")
        reveal.clicked.connect(lambda: app.open_path(app.workspace.root))
        change = PushButton("更换工作区")
        change.clicked.connect(self.change_workspace)
        row.addWidget(reveal)
        row.addWidget(change)
        row.addStretch()
        cl.addLayout(row)
        self.pending = muted("工作区采用单实例写入；更换位置将在下次启动时生效。")
        cl.addWidget(self.pending)
        box.addWidget(card)
        card, cl = panel("备份与恢复", "完整工作区备份包含所有数据集、视频、回收站和历史裁剪版本。")
        row = QHBoxLayout()
        backup = PrimaryPushButton(FIF.SAVE, "备份工作区")
        backup.clicked.connect(self.backup)
        restore = PushButton("恢复到新工作区")
        restore.clicked.connect(self.restore)
        row.addWidget(backup)
        row.addWidget(restore)
        row.addStretch()
        cl.addLayout(row)
        cl.addWidget(muted("分享单个数据集，请在素材库使用「导出 ZIP」；完整工作区备份用于本机迁移和恢复。"))
        box.addWidget(card)
        card, cl = panel("操作与诊断")
        cl.addWidget(BodyLabel("Space 拍照 / 开始或停止录制   ·   滚轮缩放   ·   双击画布适应窗口"))
        cl.addWidget(
            muted(
                "热区选中后可拖动；右下角调整尺寸；Delete 删除。\n素材库使用 Ctrl / ⌘ 或 Shift 多选。\n定时采集遇到保存较慢时跳过本次触发，避免积压图像。"
            )
        )
        logs = PushButton("打开日志文件夹")
        logs.clicked.connect(lambda: app.open_path(app.log_dir))
        cl.addWidget(logs)
        box.addWidget(card)
        card, cl = panel("关于 Vannus")
        cl.addWidget(SubtitleLabel(f"版本 {__version__}"))
        cl.addWidget(muted("Power By AJ"))
        box.addWidget(card)
        box.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.refresh()

    def refresh(self):
        usage = shutil.disk_usage(self.app.workspace.root)
        self.space.setText(
            f"剩余空间 {usage.free / 1024**3:.1f} GB  /  总容量 {usage.total / 1024**3:.1f} GB"
        )

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
        from datetime import datetime

        destination = Path(parent) / datetime.now().strftime("Vannus-恢复-%Y%m%d-%H%M%S")

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
