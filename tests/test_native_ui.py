import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QItemSelectionModel, QLockFile, QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
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


@pytest.mark.parametrize("fail", [False, True])
def test_photo_save_keeps_canvas_enabled_and_restores_preview_priority(qt_app, tmp_path, fail):
    from workbench.storage import Workspace

    workspace = Workspace(tmp_path / "async-photo")
    dataset = workspace.save_dataset("异步保存")
    workspace.save_batch(dataset, "当前批次")
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    preferences = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    window = MainWindow(workspace, preferences, tmp_path, lock)
    release = threading.Event()
    completed = []
    errors = []
    window.runner.error.connect(errors.append)

    def save(cancel, progress):
        assert release.wait(5)
        if fail:
            raise OSError("图片写入失败")
        return "saved"

    try:
        window.show()
        window.open_capture(dataset)
        assert window.start_task("保存照片", save, completed.append, photo_capture=True)
        assert window.runner.busy
        assert window.capture.camera.saving_photo
        assert window.stackedWidget.isEnabled()
        assert window.capture.canvas.isEnabled()
        assert not window.capture.options_scroll.isEnabled()
        assert not window.capture.mode_combo.isEnabled()
        assert not window.capture.shoot.isEnabled()
        assert not window.navigationInterface.isEnabled()
        release.set()
        pump(qt_app, lambda: not window.runner.busy)
        assert not window.capture.camera.saving_photo
        assert window.capture.options_scroll.isEnabled()
        assert window.capture.mode_combo.isEnabled()
        assert window.navigationInterface.isEnabled()
        assert completed == ([] if fail else ["saved"])
        assert errors == (["图片写入失败"] if fail else [])
    finally:
        release.set()
        pump(qt_app, lambda: not window.runner.busy)
        window.close()
        qt_app.processEvents()


def test_mvs_stream_failure_discards_unfinished_recording(qt_app, tmp_path, monkeypatch):
    from workbench.storage import Workspace

    workspace = Workspace(tmp_path / "stream-failure")
    dataset = workspace.save_dataset("断流检查")
    batch = workspace.save_batch(dataset, "当前批次")
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    preferences = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    monkeypatch.setenv("VANNUS_MVS_LIBRARY", str(tmp_path / "missing-sdk"))
    window = MainWindow(workspace, preferences, tmp_path, lock)
    pane = window.capture
    camera = pane.camera
    staging = workspace.new_recording_path()
    staging.write_bytes(b"unfinished recording")
    pane.recording_context = dict(dataset_id=dataset, batch_id=batch, path=str(staging))
    camera.recording_path = staging
    errors = []
    camera.video_error.connect(errors.append)
    try:
        camera.mvs.failed.emit("海康相机已断开")
        pump(qt_app, lambda: not camera.video_busy)
        assert errors == ["海康相机已断开"]
        assert pane.recording_context is None
        assert workspace.videos(dataset_id=dataset) == []
        assert not staging.parent.exists()
    finally:
        window.close()
        qt_app.processEvents()


def test_mvs_candling_applies_saved_settings_and_records_readback(qt_app, tmp_path, monkeypatch):
    from workbench.mvs import MODEL, CameraSettings, FloatControl, MvsDevice, SettingsChange
    from workbench.mvs_stream import MvsStream
    from workbench.storage import Workspace

    workspace = Workspace(tmp_path / "manual-exposure")
    dataset = workspace.save_dataset("照蛋")
    workspace.save_batch(dataset, "试拍")
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    preferences = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(MvsStream, "refresh", lambda _: None)
    window = MainWindow(workspace, preferences, tmp_path, lock)
    pane = window.capture
    stream = pane.camera.mvs
    device = MvsDevice(MODEL, "1")
    stream.devices = [device]
    monkeypatch.setattr(pane.camera.media, "videoInputs", lambda: [])
    monkeypatch.setattr(stream.backend, "open", lambda _: None)
    ordinary = CameraSettings(
        exposure=FloatControl(35_500, True, 15, 10_000_000),
        gain=FloatControl(4, True, 0, 17),
        gamma_enabled=True,
    )
    settings = ordinary
    withhold_frame = threading.Event()
    changes = []

    def read(change):
        nonlocal settings, ordinary
        if change is not None:
            changes.append(change)
            if change.candling is False:
                settings = ordinary
            if change.candling is True:
                settings = replace(settings, candling=True, gamma_enabled=False, gain=replace(settings.gain, value=0))
            if change.exposure_us is not None:
                settings = replace(settings, exposure=replace(settings.exposure, value=change.exposure_us))
            if change.gain_db is not None:
                settings = replace(settings, gain=replace(settings.gain, value=round(change.gain_db * 2) / 2))
            if not settings.candling:
                ordinary = settings
        if withhold_frame.is_set():
            return None, settings, ""
        image = QImage(4, 4, QImage.Format.Format_Grayscale8)
        image.fill(0)
        return (image, time.monotonic()), settings, ""

    monkeypatch.setattr(stream.backend, "read", read)
    try:
        window.open_capture(dataset)
        pane.refresh_devices()
        pane.connect_camera()
        pump(qt_app, lambda: stream.active)
        assert not pane.exposure_auto.isChecked()
        assert not pane.exposure_auto.isEnabled()
        assert pane.exposure.isEnabled()
        assert pane.exposure.minimum() == 0.015
        assert pane.exposure.maximum() == 10_000
        assert pane.exposure.value() == 35.5
        assert changes == []

        pane.exposure.lineEdit().selectAll()
        QTest.keyClicks(pane.exposure.lineEdit(), "55")
        assert pane.exposure.value() == 35.5
        QTest.keyClick(pane.exposure.lineEdit(), Qt.Key.Key_Return)
        pump(qt_app, lambda: pane.camera.capture_settings_ready)
        assert changes == [SettingsChange(exposure_us=55_000)]
        assert pane.camera.metadata()["exposure_time"] == 0.055

        withhold_frame.set()
        pane.candling_mode.setChecked(True)
        pump(qt_app, lambda: stream.settings.candling)
        assert not pane.shoot.isEnabled()
        with pytest.raises(ValueError, match="等待新画面"):
            pane.camera.snapshot()
        withhold_frame.clear()
        pump(qt_app, lambda: pane.shoot.isEnabled())
        assert pane.gain.value() == 0
        pane.exposure.setValue(80.5)
        pump(qt_app, lambda: pane.camera.capture_settings_ready)
        pane.gain.setValue(2.7)
        pump(qt_app, lambda: pane.camera.capture_settings_ready)
        assert pane.gain.value() == 2.5
        pane.save_candling.click()
        persisted = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
        assert persisted.value(f"capture/candling/{device.id().hex()}") == {
            "exposure_us": 80_500,
            "gain_db": 2.5,
        }

        pane.candling_mode.setChecked(False)
        pump(qt_app, lambda: pane.camera.capture_settings_ready)
        assert pane.exposure.value() == 55
        assert pane.gain.value() == 4
        pane.disconnect_camera()
        pane.connect_camera()
        pump(qt_app, lambda: stream.active)
        pane.candling_mode.setChecked(True)
        pump(qt_app, lambda: pane.camera.capture_settings_ready)
        assert changes[-1] == SettingsChange(exposure_us=80_500, gain_db=2.5, candling=True)

        pane.take_photo()
        pump(qt_app, lambda: not window.runner.busy)
        photo = workspace.photos(dataset_id=dataset)[0]
        expected = {"capture_profile": "candling", "exposure_time": 0.0805, "gain_db": 2.5}
        photo_camera = json.loads(photo["metadata"])["camera"]
        assert {key: photo_camera[key] for key in expected} == expected

        def start_recording(path):
            Path(path).write_bytes(b"video fixture")
            pane.camera.recording_path = path
            pane.camera.recording_changed.emit()

        monkeypatch.setattr(pane.camera, "start_recording", start_recording)
        pane.mode_combo.setCurrentIndex(1)
        pane.toggle_recording()
        assert not pane.gain.isEnabled()
        assert not pane.exposure.isEnabled()
        pane.set_gain(7)
        assert not stream.settings_pending
        pane.camera.recording_path = None
        pane.camera.recording_changed.emit()
        pane._video_finished(pane.recording_context["path"], 1_000)
        pump(qt_app, lambda: not window.runner.busy)
        video_camera = json.loads(workspace.videos(dataset_id=dataset)[0]["metadata"])["camera"]
        assert {key: video_camera[key] for key in expected} == expected

        pane.disconnect_camera()
        assert pane.exposure_auto.isChecked()
        assert pane.exposure.value() == 10
    finally:
        window.close()
        qt_app.processEvents()


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
