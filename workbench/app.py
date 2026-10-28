"""Desktop entry point. Run with `uv run image-workbench`."""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QLockFile, QRect, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    ProgressBar,
    PushButton,
    Theme,
    qrouter,
    setCustomStyleSheet,
    setTheme,
    setThemeColor,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from .branding import ICON_PATH, NAME, TITLE, configure_application
from .feedback import CaptureFeedback
from .jobs import TaskRunner
from .storage import Workspace
from .ui.capture import CapturePage
from .ui.common import STYLE
from .ui.home import HomePage
from .ui.library import LibraryPage, PhotoDialog
from .ui.navigation import SidebarBrand, SidebarItem
from .ui.settings import SettingsPage, TrashPage
from .ui.theme import BACKGROUND, BLUE
from .ui.typography import install_typography


class MainWindow(FluentWindow):
    def __init__(self, workspace, preferences, log_dir, workspace_lock):
        install_typography()
        setThemeColor(QColor(BLUE))
        super().__init__()
        self.workspace = workspace
        self.preferences = preferences
        self.log_dir = log_dir
        self.workspace_lock = workspace_lock
        self.runner = TaskRunner(self)
        self.feedback = CaptureFeedback(preferences, self)
        self._success = None
        self._task_label = ""
        self.setWindowTitle(TITLE)
        self.resize(1440, 940)
        self.setMinimumSize(1200, 800)
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor("#FFFFFF", "#FFFFFF")
        self.titleBar.titleLabel.hide()
        self.titleBar.iconLabel.hide()
        if sys.platform == "darwin":
            # Use AppKit's real traffic lights, including native full screen behavior.
            self.setSystemTitleBarButtonVisible(True)
            self.titleBar.minBtn.hide()
            self.titleBar.maxBtn.hide()
            self.titleBar.closeBtn.hide()
        self.navigationInterface.setExpandWidth(220)
        self.navigationInterface.setMinimumExpandWidth(1380)
        self.navigationInterface.setMenuButtonVisible(False)
        self.navigationInterface.setReturnButtonVisible(False)
        self.navigationInterface.setIndicatorAnimationEnabled(False)
        self.navigationInterface.addWidget("brand", SidebarBrand())
        self.home = HomePage(self)
        self.capture = CapturePage(self)
        self.library = LibraryPage(self)
        self.trash = TrashPage(self)
        self.settings = SettingsPage(self)
        self.addSubInterface(self.home, FIF.HOME, "数据集")
        self.addSubInterface(self.capture, FIF.CAMERA, "采集")
        self.addSubInterface(self.library, FIF.PHOTO, "素材库")
        self.addSubInterface(self.trash, FIF.DELETE, "回收站", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
        self.navigationInterface.setCurrentItem(self.home.objectName())
        self.navigationInterface.expand(useAni=False)
        self.home.acquire.connect(self.open_capture)
        self.home.browse.connect(self.open_library)
        self.setStyleSheet(self.styleSheet() + STYLE)
        surface = f"StackedWidget {{ background: {BACKGROUND}; border: none; border-top-left-radius: 28px; }}"
        setCustomStyleSheet(self.stackedWidget, surface, surface)
        sidebar_style = "NavigationPanel {background: #FFFFFF; border: none;}"
        setCustomStyleSheet(self.navigationInterface.panel, sidebar_style, sidebar_style)
        self.stackedWidget.currentChanged.connect(self._page_changed)
        self.space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.space.setAutoRepeat(False)
        self.space.activated.connect(self._space_capture)
        # A single persistent task bar is shared across all pages.
        self.task_bar = QWidget(self)
        self.task_bar.setObjectName("panel")
        bar = QVBoxLayout(self.task_bar)
        bar.setContentsMargins(18, 12, 18, 12)
        row = QHBoxLayout()
        self.task_text = BodyLabel("")
        row.addWidget(self.task_text, 1)
        cancel = PushButton("取消任务")
        cancel.clicked.connect(self.runner.cancel)
        row.addWidget(cancel)
        bar.addLayout(row)
        self.task_progress = ProgressBar()
        bar.addWidget(self.task_progress)
        self.task_bar.hide()
        self.runner.progress.connect(self._progress)
        self.runner.done.connect(self._done)
        self.runner.error.connect(self._error)
        self.runner.busy_changed.connect(self._busy_changed)

    def addSubInterface(self, interface, icon, text, position=NavigationItemPosition.TOP):
        interface.setProperty("isStackedTransparent", False)
        self.stackedWidget.addWidget(interface)
        route = interface.objectName()
        item = SidebarItem(icon, text)
        self.navigationInterface.addWidget(
            route, item, lambda: self.switchTo(interface), position=position, tooltip=text
        )
        if self.stackedWidget.count() == 1:
            self.stackedWidget.currentChanged.connect(self._onCurrentInterfaceChanged)
            self.navigationInterface.setCurrentItem(route)
            qrouter.setDefaultRouteKey(self.stackedWidget, route)
        self._updateStackedBackground()
        return item

    def systemTitleBarRect(self, size):
        return QRect(8, 0, 78, 48)

    def _space_capture(self):
        from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QTextEdit

        focus = QApplication.focusWidget()
        if self.stackedWidget.currentWidget() is self.capture and not isinstance(
            focus, (QLineEdit, QTextEdit, QAbstractSpinBox)
        ):
            self.capture.request_capture()

    def _page_changed(self, *_):
        if self.stackedWidget.currentWidget() is not self.capture:
            self.capture.stop_interval()
        if self.stackedWidget.currentWidget() is self.settings:
            self.settings.refresh()

    def refresh(self):
        self.home.refresh()
        self.capture.refresh()
        self.library.refresh()
        self.trash.refresh()
        self.settings.refresh()

    def notify(self, text, error=False):
        function = InfoBar.error if error else InfoBar.success
        function(
            title="操作未完成" if error else "已完成",
            content=text,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=6500 if error else 2800,
            parent=self,
        )

    def start_task(self, label, function, success=None):
        if self.capture.camera.video_busy:
            self.notify("请先停止录制并等待视频保存", error=True)
            return
        if self.runner.busy:
            self.notify("请等待当前任务完成", error=True)
            return
        self._success = success
        self._task_label = label
        self.task_text.setText(label)
        self.task_progress.setValue(0)
        self.runner.start(function)
        return True

    def _progress(self, current, total):
        self.task_progress.setValue(round(current / max(total, 1) * 100))
        self.task_text.setText(f"{self._task_label}  {current:,} / {total:,}")

    def _done(self, result):
        if self._success:
            try:
                self._success(result)
            except Exception as error:
                logging.exception("Task callback failed")
                self.notify(str(error), error=True)

    def _error(self, error):
        self.capture.stop_interval()
        self.refresh()
        self.notify(error, error=True)

    def _busy_changed(self, busy):
        self.task_bar.setVisible(busy)
        self.task_bar.raise_()
        self.stackedWidget.setEnabled(not busy)
        self.navigationInterface.setEnabled(not busy and not self.capture.camera.video_busy)
        if not busy:
            self._success = None
            self.capture.update_capture_controls()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "task_bar"):
            self.task_bar.setGeometry(
                max(210, self.width() // 3), self.height() - 105, min(660, self.width() - 240), 86
            )

    def open_capture(self, dataset_id, batch_id=None):
        self.capture.select_dataset(dataset_id, batch_id)
        self.switchTo(self.capture)

    def open_library(self, dataset_id, media_type="all", batch_id=None):
        self.library.select_dataset(dataset_id, media_type, batch_id)
        self.switchTo(self.library)

    def show_media(self, item):
        if item and item.get("media_type") == "video":
            self.open_path(self.workspace.path(item["original"]))
        else:
            self.show_photo(item)

    def show_photo(self, photo):
        if photo:
            PhotoDialog(self, self.workspace.photo(photo["id"])).exec()

    def open_path(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def import_archive(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入完整数据集归档", "", "ZIP 归档 (*.zip)")
        if path:

            def done(dataset_id):
                self.refresh()
                self.open_library(dataset_id)
                self.notify("数据集已恢复；使用新编号，不覆盖已有记录")

            self.start_task(
                "正在校验并导入数据集…",
                lambda cancel, progress: self.workspace.import_zip(path, cancel=cancel, progress=progress),
                done,
            )

    def closeEvent(self, event):
        if self.capture.camera.video_busy:
            self.capture.camera.stop_recording()
            self.notify("正在停止录制并保存视频，保存完成后可关闭窗口", error=True)
            event.ignore()
            return
        if self.runner.busy:
            self.notify("后台任务仍在运行，请取消或等待完成后关闭", error=True)
            event.ignore()
            return
        self.capture.stop_interval()
        self.capture.camera.stop()
        # Thumbnail jobs need their signals and pool to stay alive until completion.
        self.capture.recent_model.pool.waitForDone()
        self.home.recent_model.pool.waitForDone()
        self.library.model.pool.waitForDone()
        self.preferences.setValue("geometry", self.saveGeometry())
        self.workspace_lock.unlock()
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--workspace", type=Path, help="指定本地工作区")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    preferences = configure_application(app)
    font = QFont(
        "PingFang SC"
        if sys.platform == "darwin"
        else "Microsoft YaHei UI"
        if sys.platform == "win32"
        else "Noto Sans CJK SC"
    )
    font.setPointSize(11)
    app.setFont(font)
    setTheme(Theme.LIGHT)
    setThemeColor(QColor(BLUE))
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    root = args.workspace or Path(preferences.value("workspace", str(data_dir / "workspace")))
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace_lock = QLockFile(str(root / ".fieldwork.lock"))
    workspace_lock.setStaleLockTime(0)
    if not workspace_lock.tryLock(100):
        QMessageBox.warning(None, "工作区正在使用", "另一个应用实例正在使用此工作区，请关闭后再试。")
        return 1
    log_dir = data_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / f"{NAME.lower()}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        workspace = Workspace(root)
        window = MainWindow(workspace, preferences, log_dir, workspace_lock)
        geometry = preferences.value("geometry")
        if geometry:
            window.restoreGeometry(geometry)
        window.show()
        return app.exec()
    except Exception as error:
        logging.exception("Application startup failed")
        QMessageBox.critical(None, "启动失败", str(error))
        workspace_lock.unlock()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
