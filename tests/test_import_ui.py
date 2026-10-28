import json
import time

import pytest
from PIL import Image
from PySide6.QtCore import QLockFile, QSettings, QTimer

from workbench.app import MainWindow
from workbench.storage import Workspace
from workbench.ui.library import ImportImagesDialog


@pytest.fixture
def import_window(qt_app, tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    preferences = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    window = MainWindow(workspace, preferences, tmp_path, lock)
    window.show()
    window.switchTo(window.library)
    qt_app.processEvents()
    yield window
    window.close()
    qt_app.processEvents()


def operate_import_dialog(window, operation):
    errors = []

    def act():
        dialog = next(
            (dialog for dialog in window.findChildren(ImportImagesDialog) if dialog.isVisible()), None
        )
        try:
            assert isinstance(dialog, ImportImagesDialog)
            operation(dialog)
        except Exception as error:
            errors.append(error)
            if dialog:
                dialog.reject()

    # Finish Fluent's entrance animation before clicking its native dialog controls.
    QTimer.singleShot(300, act)
    return errors


@pytest.mark.parametrize(
    "scenario",
    ["empty", "no_batch", "existing_batch", "new_batch", "new_dataset", "unselected", "archived"],
)
def test_import_resolves_destination_in_place(import_window, qt_app, tmp_path, monkeypatch, scenario):
    window = import_window
    workspace = window.workspace
    dataset_id, batch_id = None, None
    defaults = {"note": "批次备注", "custom": {"温度": "25°C"}}
    if scenario != "empty":
        dataset_id = workspace.save_dataset("已有数据集")
        if scenario != "no_batch":
            batch_id = workspace.save_batch(dataset_id, "目标批次", defaults)
            workspace.save_batch(dataset_id, "其他批次")
        if scenario == "archived":
            workspace.archive_dataset(dataset_id, True)
    window.refresh()
    window.library.select_dataset(dataset_id, "video", batch_id)
    if scenario == "unselected":
        window.library.select_dataset(None)
    window.library.search.setText("不会匹配导入图片的搜索")
    window.library.date_combo.setCurrentIndex(1)
    source = tmp_path / "输入图片.png"
    Image.new("RGB", (12, 8), "teal").save(source)
    monkeypatch.setattr(
        "workbench.ui.library.QFileDialog.getOpenFileNames", lambda *args: ([str(source)], "")
    )
    notifications = []
    monkeypatch.setattr(window, "notify", lambda text, error=False: notifications.append((text, error)))

    def accept(dialog):
        if scenario in {"empty", "new_dataset"}:
            dialog.dataset_combo.setCurrentIndex(dialog.dataset_combo.findData(None))
            assert not dialog.validate()
            dialog.dataset_name.setText("新建导入数据集")
        else:
            assert dialog.selected_dataset()["id"] == dataset_id
        if scenario in {"empty", "no_batch", "new_batch", "new_dataset"}:
            dialog.batch_combo.setCurrentIndex(dialog.batch_combo.findData(None))
            dialog.batch_name.setText("本次导入")
        elif scenario == "unselected":
            dialog.batch_combo.setCurrentIndex(dialog.batch_combo.findData(batch_id))
        else:
            assert dialog.batch_combo.currentData() == batch_id
        if scenario == "archived":
            assert dialog.yesButton.text() == "取消归档并导入"
            assert workspace.datasets()[0]["archived"]
        assert dialog.validate()
        dialog.yesButton.click()

    errors = operate_import_dialog(window, accept)
    window.library.import_images()
    assert not errors, errors
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and window.runner.busy:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert not window.runner.busy
    assert workspace.count_photos() == 1, notifications
    photo = workspace.photos()[0]
    assert Image.open(workspace.path(photo["original"])).size == (12, 8)
    assert window.library.dataset_id() == photo["dataset_id"]
    assert window.library.batch_combo.currentData() == photo["batch_id"]
    assert window.library.type_tabs.currentRouteKey() == "photo"
    assert window.library.model.total == 1
    assert not window.library.search.text()
    assert window.library.date_combo.currentIndex() == 0
    if scenario in {"empty", "new_dataset"}:
        assert photo["dataset_id"] != dataset_id
        assert photo["dataset_name"] == "新建导入数据集"
    else:
        assert photo["dataset_id"] == dataset_id
    if scenario in {"existing_batch", "unselected", "archived"}:
        assert photo["batch_id"] == batch_id
        assert photo["note"] == defaults["note"]
        assert json.loads(photo["metadata"])["custom"] == defaults["custom"]
    else:
        assert photo["batch_id"] != batch_id
        assert photo["batch_name"] == "本次导入"
    assert any("已导入 1 张图片" in text and not error for text, error in notifications)
    assert not any(error for _, error in notifications)
    if scenario == "archived":
        assert not workspace.datasets()[0]["archived"]


@pytest.mark.parametrize("archived", [False, True])
def test_cancel_destination_keeps_workspace_unchanged(import_window, monkeypatch, tmp_path, archived):
    window = import_window
    workspace = window.workspace
    dataset_id = workspace.save_dataset("归档数据集") if archived else None
    if archived:
        workspace.archive_dataset(dataset_id, True)
    window.refresh()
    window.library.select_dataset(dataset_id)
    before = workspace.datasets()
    monkeypatch.setattr(
        "workbench.ui.library.QFileDialog.getOpenFileNames", lambda *args: ([str(tmp_path / "image.png")], "")
    )

    def cancel(dialog):
        dialog.dataset_name.setText("取消后不创建")
        dialog.batch_name.setText("取消后不创建")
        dialog.cancelButton.click()

    errors = operate_import_dialog(window, cancel)
    window.library.import_images()
    assert not errors, errors
    assert not window.runner.busy
    assert workspace.datasets() == before
    assert not workspace.batches(dataset_id)
    assert workspace.count_photos() == 0


def test_cancel_file_picker_does_not_start_setup(import_window, monkeypatch):
    window = import_window
    monkeypatch.setattr("workbench.ui.library.QFileDialog.getOpenFileNames", lambda *args: ([], ""))
    window.library.import_images()
    assert not window.runner.busy
    assert not window.workspace.datasets()
