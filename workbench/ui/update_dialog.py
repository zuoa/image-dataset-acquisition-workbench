"""Modal Fluent dialog for inspecting a GitHub Release and applying an in-app update."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    IndeterminateProgressBar,
    MessageBoxBase,
    ProgressBar,
    PushButton,
    SubtitleLabel,
    TextEdit,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from ..timeutils import format_datetime
from ..updater import StagedUpdate, UpdateCheckResult, format_bytes
from .common import muted
from .theme import (
    BORDER_SUBTLE,
    DANGER,
    DANGER_BG,
    DANGER_BORDER,
    INK,
    SURFACE_SUBTLE,
    WARNING,
    WARNING_BG,
    WARNING_BORDER,
    SoftCard,
    StatusPill,
)


class UpdateDialog(MessageBoxBase):
    """Displays release notes, handles verified download progress, and hands off restart."""

    def __init__(self, app, result: UpdateCheckResult, parent=None):
        super().__init__(parent or app.window())
        assert result.latest_release is not None
        self.app = app
        self.result = result
        self.release = result.latest_release
        self._staged: StagedUpdate | None = None
        self._busy = False
        self._signals_connected = False

        self.widget.setMinimumWidth(520)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(SubtitleLabel("发现新版本", self))
        header.addStretch()
        header.addWidget(StatusPill(f"当前 v{result.current_version.display}", "neutral", dot=False, parent=self))
        arrow = muted("→")
        header.addWidget(arrow)
        header.addWidget(StatusPill(f"最新 v{self.release.version.display}", "info", dot=False, parent=self))
        self.viewLayout.addLayout(header)

        meta_parts = []
        if self.release.published_at:
            meta_parts.append(f"发布于 {format_datetime(self.release.published_at)}")
        if self.release.asset and self.release.asset.size > 0:
            meta_parts.append(f"安装包 {format_bytes(self.release.asset.size)}")
        if meta_parts:
            self.viewLayout.addWidget(muted(" · ".join(meta_parts)))

        self.notes = TextEdit(self)
        self.notes.setReadOnly(True)
        self.notes.setFixedHeight(210)
        self.notes.setStyleSheet(
            f"""
            TextEdit {{
                background: {SURFACE_SUBTLE};
                color: {INK};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 10px;
                padding: 8px 10px;
            }}
            """
        )
        if self.release.body:
            self.notes.setMarkdown(self.release.body)
        else:
            self.notes.setPlainText("本次发布未提供更新说明。")
        self.viewLayout.addWidget(self.notes)

        self.notice_card = SoftCard(
            self,
            tint=WARNING_BG,
            border=WARNING_BORDER,
            radius=12,
            shadow=False,
        )
        notice_layout = QVBoxLayout(self.notice_card)
        notice_layout.setContentsMargins(14, 10, 14, 10)
        notice_layout.setSpacing(4)
        self.notice_label = BodyLabel(result.fallback_reason or "", self.notice_card)
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet(f"color: {WARNING};")
        notice_layout.addWidget(self.notice_label)
        self.notice_card.setVisible(not result.can_auto_install and bool(result.fallback_reason))
        self.viewLayout.addWidget(self.notice_card)

        self.error_card = SoftCard(
            self,
            tint=DANGER_BG,
            border=DANGER_BORDER,
            radius=12,
            shadow=False,
        )
        error_layout = QVBoxLayout(self.error_card)
        error_layout.setContentsMargins(14, 10, 14, 10)
        self.error_label = BodyLabel("", self.error_card)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {DANGER};")
        error_layout.addWidget(self.error_label)
        self.error_card.hide()
        self.viewLayout.addWidget(self.error_card)

        self.progress_widget = QWidget(self)
        progress_layout = QVBoxLayout(self.progress_widget)
        progress_layout.setContentsMargins(0, 4, 0, 0)
        progress_layout.setSpacing(6)
        self.progress_label = muted("")
        progress_layout.addWidget(self.progress_label)
        self.progress_bar = ProgressBar(self.progress_widget)
        self.progress_bar.setRange(0, 100)
        progress_layout.addWidget(self.progress_bar)
        self.indeterminate_bar = IndeterminateProgressBar(self.progress_widget)
        self.indeterminate_bar.hide()
        progress_layout.addWidget(self.indeterminate_bar)
        self.progress_widget.hide()
        self.viewLayout.addWidget(self.progress_widget)

        extra_row = QHBoxLayout()
        extra_row.setSpacing(8)
        self.ignore_button = PushButton("忽略此版本", self)
        self.ignore_button.clicked.connect(self._on_ignore_clicked)
        self.release_page_button = PushButton(FIF.GLOBE, "打开发布页", self)
        self.release_page_button.clicked.connect(self._on_open_release_page)
        extra_row.addWidget(self.ignore_button)
        extra_row.addWidget(self.release_page_button)
        extra_row.addStretch()
        self.viewLayout.addLayout(extra_row)

        self.yesButton.clicked.disconnect()
        self.cancelButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_primary_clicked)
        self.cancelButton.clicked.connect(self._on_cancel_clicked)

        self._reset_idle_buttons()
        self._connect_updater()

    def _connect_updater(self) -> None:
        if self._signals_connected:
            return
        updater = self.app.updater
        updater.stage_changed.connect(self._on_stage_changed)
        updater.download_progress.connect(self._on_download_progress)
        updater.update_ready.connect(self._on_update_ready)
        updater.update_failed.connect(self._on_update_failed)
        updater.update_cancelled.connect(self._on_update_cancelled)
        self._signals_connected = True

    def _disconnect_updater(self) -> None:
        if not self._signals_connected:
            return
        updater = self.app.updater
        for signal, slot in (
            (updater.stage_changed, self._on_stage_changed),
            (updater.download_progress, self._on_download_progress),
            (updater.update_ready, self._on_update_ready),
            (updater.update_failed, self._on_update_failed),
            (updater.update_cancelled, self._on_update_cancelled),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._signals_connected = False

    def _reset_idle_buttons(self) -> None:
        self._busy = False
        self.ignore_button.setEnabled(True)
        self.release_page_button.setEnabled(True)
        self.yesButton.setEnabled(True)
        if self._staged is not None:
            self.yesButton.setText("重启并安装")
            self.cancelButton.setText("稍后安装")
        elif self.result.can_auto_install:
            self.yesButton.setText("立即更新")
            self.cancelButton.setText("稍后提醒")
        else:
            self.yesButton.setText("打开发布页")
            self.cancelButton.setText("稍后提醒")

    def _set_indeterminate(self, active: bool) -> None:
        self.progress_bar.setVisible(not active)
        self.indeterminate_bar.setVisible(active)
        if active:
            self.indeterminate_bar.start()
        else:
            self.indeterminate_bar.stop()

    def _on_primary_clicked(self) -> None:
        if self._busy:
            return
        if self._staged is not None:
            if self.app.apply_staged_update(self._staged):
                self.accept()
            return
        if not self.result.can_auto_install:
            self._on_open_release_page()
            self.accept()
            return

        self.error_card.hide()
        self.progress_widget.show()
        self._busy = True
        self.yesButton.setEnabled(False)
        self.yesButton.setText("正在更新…")
        self.cancelButton.setText("取消下载")
        self.ignore_button.setEnabled(False)
        self._set_indeterminate(True)
        self.progress_label.setText("正在获取校验文件…")
        if not self.app.updater.start_download(self.release):
            self._reset_idle_buttons()

    def _on_cancel_clicked(self) -> None:
        if self._busy:
            self.cancelButton.setEnabled(False)
            self.progress_label.setText("正在取消更新…")
            self.app.updater.cancel_download()
            return
        self.reject()

    def _on_ignore_clicked(self) -> None:
        if self._busy:
            return
        self.app.updater.ignore_version(self.release.version.display)
        self.app.update_nav_badge()
        self.reject()

    def _on_open_release_page(self) -> None:
        self.app.open_url(self.release.html_url)

    def _on_stage_changed(self, stage: str) -> None:
        self.progress_widget.show()
        if stage == "checksum":
            self._set_indeterminate(True)
            self.progress_label.setText("正在获取校验文件…")
        elif stage == "downloading":
            asset_name = self.release.asset.name if self.release.asset else "安装包"
            self._set_indeterminate(False)
            self.progress_bar.setValue(0)
            self.progress_label.setText(f"正在下载 {asset_name}…")
        elif stage == "verifying":
            self._set_indeterminate(True)
            self.progress_label.setText("正在校验 SHA256 完整性…")
        elif stage == "extracting":
            self._set_indeterminate(True)
            self.progress_label.setText("正在解压并验证安装包…")
        elif stage == "ready":
            self._set_indeterminate(False)
            self.progress_bar.setValue(100)
            self.progress_label.setText("更新包校验通过，准备重启安装…")

    def _on_download_progress(self, received: int, total: int) -> None:
        asset_name = self.release.asset.name if self.release.asset else "安装包"
        if total > 0:
            self._set_indeterminate(False)
            pct = min(100, max(0, round(received / total * 100)))
            self.progress_bar.setValue(pct)
            self.progress_label.setText(
                f"正在下载 {asset_name}… {format_bytes(received)} / {format_bytes(total)} ({pct}%)"
            )
        else:
            self._set_indeterminate(True)
            self.progress_label.setText(f"正在下载 {asset_name}… {format_bytes(received)}")

    def _on_update_ready(self, staged: StagedUpdate) -> None:
        self._staged = staged
        self._busy = False
        self.cancelButton.setEnabled(True)
        if self.app.apply_staged_update(staged):
            self.accept()
            return
        self._reset_idle_buttons()
        self.progress_label.setText("更新包已就绪，请结束当前采集/后台任务后点击「重启并安装」。")

    def _on_update_failed(self, message: str) -> None:
        self._set_indeterminate(False)
        self.progress_widget.hide()
        self.error_label.setText(message)
        self.error_card.show()
        self.cancelButton.setEnabled(True)
        self._reset_idle_buttons()

    def _on_update_cancelled(self) -> None:
        self._set_indeterminate(False)
        self.progress_widget.hide()
        self.cancelButton.setEnabled(True)
        self._reset_idle_buttons()

    def reject(self) -> None:
        if self._busy:
            self.app.updater.cancel_download()
        super().reject()

    def done(self, result: int) -> None:
        self._set_indeterminate(False)
        self._disconnect_updater()
        super().done(result)
