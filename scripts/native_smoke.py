"""Exercise actual native Qt windows, without opening or simulating a camera."""

import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PySide6.QtCore import QLockFile, QSettings, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor

from workbench.app import MainWindow
from workbench.storage import Workspace
from workbench.ui.common import FormDialog
from workbench.ui.library import ImportImagesDialog, PhotoDialog

root = Path(__file__).resolve().parents[1]
output = root / "build/screenshots"
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setFont(QFont("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI", 11))
setTheme(Theme.LIGHT)
setThemeColor(QColor("#087F9C"))
workspace_dir = tempfile.TemporaryDirectory(prefix="vannus-native-")
workspace = Workspace(Path(workspace_dir.name) / "workspace")
lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
assert lock.tryLock(100)
settings = QSettings(str(Path(workspace_dir.name) / "settings.ini"), QSettings.Format.IniFormat)
window = MainWindow(workspace, settings, Path(workspace_dir.name), lock)
window.show()
errors = []


def error_hook(kind, error, tb):
    errors.append(str(error))
    traceback.print_exception(kind, error, tb)


sys.excepthook = error_hook
steps = []


def screenshot(page, name):
    window.switchTo(page)

    # Fluent's page entrance animation must finish before visual inspection.
    def grab():
        assert window.grab().save(str(output / name))

    QTimer.singleShot(300, grab)


def populate():
    first = workspace.save_dataset(
        "番茄表面 · 实验归档", "不同光照条件下的表面图像采集与记录。", "番茄,自然光"
    )
    batch = workspace.save_batch(first, "自然光 · 第一批", {"note": "室温 25°C", "custom": {"温度": "25°C"}})
    workspace.add_photo(Image.new("RGB", (120, 80), "teal"), first, batch, sample_id="P-001")
    source = workspace.new_recording_path()
    # Opaque archive fixture for browsing only; this script never opens a player.
    source.write_bytes(b"native media library archive fixture")
    workspace.add_video(source, first, batch, duration_ms=12500, sample_id="V-001", note="视频浏览验收")
    workspace.save_dataset("叶片纹理观察", "叶片正反面与纹理细节归档。", "叶片,纹理")
    workspace.save_dataset("实验装置校准", "保留装置设置与采集条件记录。", "装置")
    deleted_dataset = workspace.save_dataset("误删的数据集")
    deleted_batch = workspace.save_batch(deleted_dataset, "误删的批次")
    deleted_photo = workspace.add_photo(
        Image.new("RGB", (12, 8), "teal"), deleted_dataset, deleted_batch, sample_id="P-RECOVERY-001"
    )
    source = workspace.new_recording_path()
    source.write_bytes(b"native trash archive fixture")
    deleted_video = workspace.add_video(
        source, deleted_dataset, deleted_batch, duration_ms=1000, sample_id="V-RECOVERY-001"
    )
    for kind, record_id in (
        ("photo", deleted_photo),
        ("video", deleted_video),
        ("batch", deleted_batch),
        ("dataset", deleted_dataset),
    ):
        workspace.trash(kind, [record_id])
    window.refresh()
    window.capture.select_dataset(first)
    window.library.select_dataset(first)
    assert window.capture.batch_id()


def form_check():
    dialog = FormDialog(window, "新建数据集", [("name", "名称", False), ("description", "说明", True)])
    dialog.fields["name"].setText("实验图像归档")
    dialog.fields["description"].setPlainText("采集原图与热区裁剪，保留每次实验的完整记录。")
    dialog.show()

    def grab_dialog():
        assert dialog.validate()
        assert window.grab().save(str(output / "06-dialog.png"))
        dialog.reject()

    QTimer.singleShot(280, grab_dialog)


def import_setup_check(new_dataset):
    window.switchTo(window.library)
    dataset_id = None
    if not new_dataset:
        dataset_id = next(
            dataset["id"] for dataset in workspace.datasets() if dataset["name"] == "叶片纹理观察"
        )
    dialog = ImportImagesDialog(window.library, 3, dataset_id)
    if new_dataset:
        dialog.dataset_combo.setCurrentIndex(dialog.dataset_combo.findData(None))
        dialog.dataset_name.setText("番茄自然光实验")
    dialog.show()

    def grab_dialog():
        assert dialog.validate()
        name = "14-import-new-dataset.png" if new_dataset else "15-import-new-batch.png"
        assert window.grab().save(str(output / name))
        dialog.reject()

    QTimer.singleShot(280, grab_dialog)


def photo_detail_check(minimum=False):
    dialog = PhotoDialog(window, workspace.photos()[0])
    if minimum:
        dialog.resize(750, 550)
    dialog.show()

    def grab_dialog():
        name = "17-photo-detail-minimum.png" if minimum else "16-photo-detail.png"
        assert dialog.grab().save(str(output / name))
        dialog.reject()

    QTimer.singleShot(280, grab_dialog)


def minimum_check():
    window.resize(1200, 800)
    screenshot(window.capture, "07-capture-minimum.png")
    assert window.capture.canvas.width() >= 280


def video_check():
    window.capture.mode_combo.setCurrentIndex(1)
    screenshot(window.capture, "08-video-capture.png")
    assert not window.capture.canvas.editable
    assert window.capture.shooting_options.isHidden()
    assert window.capture.shoot.text() == "开始录制  Space"


def minimum_video_check():
    window.resize(1200, 800)
    screenshot(window.capture, "10-video-minimum.png")


def video_library_check():
    window.capture.open_videos()
    assert window.library.type_tabs.currentRouteKey() == "video"
    assert window.library.model.total == 1
    window.library.view.setCurrentIndex(window.library.model.index(0))
    screenshot(window.library, "09-library-video.png")


def minimum_library_check():
    window.resize(1200, 800)
    window.library.type_tabs.setCurrentItem("all")
    screenshot(window.library, "13-media-library-minimum.png")


def minimum_trash_check():
    window.resize(1200, 800)
    assert window.trash.list.count() == 4
    window.trash.list.setCurrentRow(0)
    screenshot(window.trash, "22-trash-minimum.png")


def native_controls_check():
    if sys.platform != "darwin":
        return
    import AppKit
    import objc

    native = objc.objc_object(c_void_p=int(window.winId())).window()
    buttons = [
        native.standardWindowButton_(kind)
        for kind in (AppKit.NSWindowCloseButton, AppKit.NSWindowMiniaturizeButton, AppKit.NSWindowZoomButton)
    ]
    assert all(not button.isHidden() for button in buttons)
    assert buttons[0].frame().origin.x < buttons[1].frame().origin.x < buttons[2].frame().origin.x < 100
    # QWidget.grab omits AppKit's controls; capture this window from the compositor.
    capture = subprocess.run(
        ["screencapture", "-x", "-l", str(native.windowNumber()), str(output / "11-native-window.png")],
        capture_output=True,
        text=True,
    )
    print("Native AppKit controls visible at upper left; compositor screenshot:", capture.returncode == 0)


steps.extend(
    [
        lambda: screenshot(window.home, "00-empty.png"),
        populate,
        lambda: screenshot(window.home, "01-datasets.png"),
        lambda: screenshot(window.capture, "02-capture.png"),
        lambda: screenshot(window.library, "03-library.png"),
        lambda: screenshot(window.trash, "04-trash.png"),
        lambda: screenshot(window.settings, "05-settings.png"),
        form_check,
        minimum_check,
        video_check,
        video_library_check,
        minimum_video_check,
        native_controls_check,
        minimum_library_check,
        lambda: import_setup_check(True),
        lambda: import_setup_check(False),
        photo_detail_check,
        lambda: photo_detail_check(True),
        minimum_trash_check,
    ]
)


def next_step():
    try:
        if steps:
            steps.pop(0)()
            QTimer.singleShot(350, next_step)
        else:
            print(
                "Camera devices (not activated):", [d.description() for d in window.capture.camera.devices()]
            )
            print("Native screenshots:", output)
            window.close()
            app.exit(1 if errors else 0)
    except Exception:
        traceback.print_exc()
        window.close()
        app.exit(1)


QTimer.singleShot(500, next_step)
result = app.exec()
workspace_dir.cleanup()
raise SystemExit(result)
