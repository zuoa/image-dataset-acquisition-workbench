"""Native frame conversion fixtures; these tests do not open or simulate a camera."""

import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QtVideo, QVideoFrame

from workbench import camera


def pump(app, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_frame_conversion_preserves_original_pixels_and_presentation(qt_app):
    image = QImage(3, 2, QImage.Format.Format_RGB32)
    colors = ("red", "green", "blue", "cyan", "magenta", "yellow")
    for i, color in enumerate(colors):
        image.setPixelColor(i % 3, i // 3, QColor(color))
    frame = QVideoFrame(image)
    assert camera._convert_frame(frame).convertToFormat(image.format()) == image
    frame.setRotation(QtVideo.Rotation.Clockwise90)
    frame.setMirrored(True)
    converted = camera._convert_frame(frame)
    assert converted.size().toTuple() == (2, 3)
    assert [converted.pixelColor(x, y).name() for y in range(3) for x in range(2)] == [
        QColor(colors[i]).name() for i in (0, 3, 1, 4, 2, 5)
    ]


def test_slow_conversion_keeps_gui_responsive_and_discards_disconnected_result(qt_app, monkeypatch):
    controller = camera.CameraController()
    image = QImage(30, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    native_frame = QVideoFrame(image)
    entered = threading.Event()
    release = threading.Event()
    threads = []
    delivered = []
    ticks = []
    original = camera._convert_frame

    def slow_conversion(frame):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return original(frame)

    monkeypatch.setattr(camera, "_convert_frame", slow_conversion)
    controller.frame.connect(delivered.append)
    try:
        controller._pending_frame = (native_frame, time.monotonic())
        controller._process_frame()
        pump(qt_app, entered.is_set)
        QTimer.singleShot(0, lambda: ticks.append(True))
        for _ in range(100):
            controller._pending_frame = (native_frame, time.monotonic())
            controller._process_frame()
        pump(qt_app, lambda: bool(ticks))
        assert len(threads) == 1
        assert threads[0] != threading.get_ident()
        controller.stop()
        release.set()
        pump(qt_app, lambda: controller._frame_job is None)
        assert not delivered
        with pytest.raises(ValueError, match="暂无可采集"):
            controller.snapshot()

        controller._pending_frame = (native_frame, time.monotonic())
        controller._process_frame()
        pump(qt_app, lambda: bool(delivered))
        snapshot = controller.snapshot()
        controller.latest.fill(QColor("blue"))
        assert snapshot.pixelColor(0, 0) == QColor("red")
        controller.frame_at = time.monotonic() - 3
        with pytest.raises(ValueError, match="暂无可采集"):
            controller.snapshot()
    finally:
        release.set()
        controller.stop()
        controller.frame_pool.waitForDone()
        qt_app.processEvents()
