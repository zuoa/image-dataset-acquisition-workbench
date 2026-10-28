import sys
import time

from PIL import Image
from PySide6.QtCore import QItemSelectionModel, QLockFile, QSettings
from PySide6.QtWidgets import QListView

from workbench.app import MainWindow
from workbench.ui.common import FormDialog
from workbench.ui.models import ROLE


def pump(qt_app, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not predicate():
        qt_app.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_native_window_navigation_models_and_background_delivery(qt_app, tmp_path):
    from qfluentwidgets import Theme, setTheme

    from workbench.storage import Workspace

    setTheme(Theme.LIGHT)
    workspace = Workspace(tmp_path / "实验工作区")
    dataset = workspace.save_dataset("原生界面验收", "中文长名称与批次检查")
    batch = workspace.save_batch(dataset, "第一批")
    workspace.add_photo(Image.new("RGB", (120, 80), "teal"), dataset, batch, sample_id="T-001")
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    preferences = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    window = MainWindow(workspace, preferences, tmp_path, lock)
    try:
        window.show()
        qt_app.processEvents()
        if sys.platform == "darwin":
            import AppKit
            import objc

            native = objc.objc_object(c_void_p=int(window.winId())).window()
            buttons = [
                native.standardWindowButton_(kind)
                for kind in (
                    AppKit.NSWindowCloseButton,
                    AppKit.NSWindowMiniaturizeButton,
                    AppKit.NSWindowZoomButton,
                )
            ]
            assert all(not button.isHidden() for button in buttons)
            assert (
                buttons[0].frame().origin.x < buttons[1].frame().origin.x < buttons[2].frame().origin.x < 100
            )
            assert window.titleBar.closeBtn.isHidden()
            assert window.titleBar.minBtn.isHidden()
            assert window.titleBar.maxBtn.isHidden()
        window.open_capture(dataset)
        assert window.capture.dataset_id() == dataset
        assert window.capture.batch_id() == batch
        assert window.capture.camera_options.content.isHidden()
        assert window.capture.shooting_options.content.isHidden()
        window.capture.shooting_options.set_expanded(True)
        pump(qt_app, lambda: window.capture.countdown.lineEdit().width() > 20)
        assert window.capture.interval.lineEdit().width() > 20
        window.capture.shooting_options.set_expanded(False)
        window.capture.mode_combo.setCurrentIndex(1)
        qt_app.processEvents()
        assert window.capture.video_hint.isVisible()
        assert window.capture.shooting_options.isHidden()
        assert window.capture.crop_tools.isHidden()
        assert not window.capture.canvas.editable
        assert window.capture.shoot.text() == "开始录制  Space"
        assert not window.capture.shoot.isEnabled()  # No hardware was opened.
        window.capture.open_videos()
        assert window.library.dataset_id() == dataset
        assert window.library.type_tabs.currentRouteKey() == "video"
        assert window.library.model.rows == []
        window.open_capture(dataset)
        window.capture.mode_combo.setCurrentIndex(0)
        assert window.capture.canvas.editable
        assert window.capture.recent_model.total == 1
        window.settings.capture_sound.setChecked(False)
        preferences.sync()
        persisted = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
        assert persisted.value("capture/sound_enabled", True, type=bool) is False
        assert not window.feedback.enabled
        window.open_library(dataset)
        qt_app.processEvents()
        filters = [
            window.library.dataset_combo,
            window.library.batch_combo,
            window.library.date_combo,
            window.library.search,
        ]
        for left, right in zip(filters, filters[1:]):
            assert left.geometry().right() < right.geometry().left()
        model = window.library.model
        assert model.rowCount() == 1
        assert model.index(0).data(ROLE)["sample_id"] == "T-001"
        window.library.view.setCurrentIndex(model.index(0))
        qt_app.processEvents()
        assert window.library.selected()[0]["sample_id"] == "T-001"
        dialog = FormDialog(window, "新建数据集", [("name", "名称", False)])
        assert not dialog.validate()
        dialog.fields["name"].setText("有效名称")
        assert dialog.validate()
        dialog.reject()
        delivered = []
        window.start_task("验收后台任务", lambda cancel, progress: "done", delivered.append)
        pump(qt_app, lambda: not window.runner.busy)
        assert delivered == ["done"]
        assert window.stackedWidget.isEnabled()
        assert isinstance(window.library.view, QListView)
    finally:
        window.close()
        qt_app.processEvents()


def test_custom_fields_form_has_named_rows_and_blocks_duplicate_keys(qt_app):
    from PySide6.QtWidgets import QTableWidgetItem, QWidget

    parent = QWidget()
    dialog = FormDialog(parent, "实验信息", [("custom", "自定义信息", True)], {"custom": {"温度": "25°C"}})
    editor = dialog.fields["custom"]
    assert dialog.values()["custom"] == {"温度": "25°C"}
    editor.table.insertRow(1)
    editor.table.setItem(1, 0, QTableWidgetItem("温度"))
    editor.table.setItem(1, 1, QTableWidgetItem("26°C"))
    assert not dialog.validate()
    dialog.reject()
    parent.close()


def test_unified_library_filters_actions_and_capture_context(qt_app, tmp_path, monkeypatch):
    from workbench.storage import Workspace

    workspace = Workspace(tmp_path / "mixed-workspace")
    dataset = workspace.save_dataset("图片与视频")
    batch = workspace.save_batch(dataset, "当前批次", {"note": "批次默认备注"})
    other_batch = workspace.save_batch(dataset, "其他批次")
    photo_id = workspace.add_photo(Image.new("RGB", (120, 80), "teal"), dataset, batch, sample_id="P-001")
    workspace.add_photo(Image.new("RGB", (80, 120), "blue"), dataset, other_batch, sample_id="P-002")
    source = workspace.new_recording_path()
    source.write_bytes(b"opaque archive fixture, not a camera or playback simulation")
    video_id = workspace.add_video(
        source, dataset, batch, duration_ms=6500, sample_id="V-001", metadata={"custom": {"温度": "25°C"}}
    )
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    preferences = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    window = MainWindow(workspace, preferences, tmp_path, lock)
    try:
        window.show()
        window.resize(1200, 800)
        window.open_library(dataset)
        pump(qt_app, lambda: window.stackedWidget.currentWidget() is window.library)
        library = window.library
        assert window.stackedWidget.count() == 5
        assert library.model.total == 3
        assert library.model.counts == {"photo": 2, "video": 1}
        assert window.home.recent_model.total == 3
        library.batch_combo.setCurrentIndex(library.batch_combo.findData(batch))
        assert library.model.total == 2

        def select(item_id):
            index = next(i for i, row in enumerate(library.model.rows) if row["id"] == item_id)
            library.view.setCurrentIndex(library.model.index(index))
            qt_app.processEvents()

        select(video_id)
        assert library.play_button.isVisible()
        assert library.reveal_button.isVisible()
        assert not library.edit_button.isVisible()
        assert not library.photo_actions.isVisible()
        assert "00:00:06" in library.selection_details.text()
        assert "温度：25°C" in library.selection_details.text()
        opened = []
        monkeypatch.setattr(window, "open_path", opened.append)
        library.play_button.click()
        library.reveal_button.click()
        video_path = workspace.path(workspace.videos()[0]["original"])
        assert opened == [video_path, video_path.parent]

        select(photo_id)
        assert library.edit_button.isVisible()
        assert library.photo_actions.isVisible()
        assert not library.play_button.isVisible()
        video_index = next(i for i, row in enumerate(library.model.rows) if row["id"] == video_id)
        library.view.selectionModel().select(
            library.model.index(video_index), QItemSelectionModel.SelectionFlag.Select
        )
        assert len(library.selected()) == 2
        assert not library.edit_button.isVisible()
        assert not library.photo_actions.isVisible()
        assert not library.play_button.isVisible()

        library.type_tabs.setCurrentItem("video")
        assert library.batch_combo.currentData() == batch
        assert library.model.total == 1
        library.search.setText("missing")
        pump(qt_app, lambda: library.model.total == 0)
        library.search.clear()
        pump(qt_app, lambda: library.model.total == 1)
        library.continue_button.click()
        pump(qt_app, lambda: window.stackedWidget.currentWidget() is window.capture)
        assert window.capture.dataset_id() == dataset
        assert window.capture.batch_id() == batch
        assert window.capture.note.toPlainText() == "批次默认备注"
        assert window.capture.video_mode
        window.capture.open_videos()
        pump(qt_app, lambda: window.stackedWidget.currentWidget() is library)
        assert library.batch_combo.currentData() == batch
        library.type_tabs.setCurrentItem("photo")
        library.continue_button.click()
        pump(qt_app, lambda: window.stackedWidget.currentWidget() is window.capture)
        assert not window.capture.video_mode

        window.open_library(dataset, batch_id=batch)
        pump(qt_app, lambda: window.stackedWidget.currentWidget() is library)
        library.view.selectAll()
        monkeypatch.setattr("workbench.ui.library.confirm", lambda *args: True)
        library.delete_selected()
        assert workspace.media_counts(dataset_id=dataset, batch_id=batch) == {"photo": 0, "video": 0}
        assert workspace.count_photos(dataset_id=dataset) == 1
        workspace.restore("photo", photo_id)
        workspace.restore("video", video_id)
        window.refresh()
        assert library.model.total == 2

        workspace.archive_dataset(dataset, True)
        window.refresh()
        assert not library.continue_button.isEnabled()
        assert library.model.total == 2
    finally:
        window.close()
        qt_app.processEvents()
