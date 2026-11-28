"""Camera controller and SDK boundary regressions without opening hardware."""

import ctypes
import threading
import time
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QtVideo, QVideoFrame

from workbench import camera, mvs, mvs_stream


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


def test_preview_is_bounded_while_snapshot_keeps_full_resolution(qt_app):
    controller = camera.CameraController()
    image = QImage(1600, 1200, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    previews = []
    controller.preview.connect(lambda preview, size: previews.append((preview, size)))
    try:
        controller._pending_frame = (QVideoFrame(image), time.monotonic())
        controller.set_saving_photo(True)
        controller._process_frame()
        assert controller._frame_job is None
        controller.set_saving_photo(False)
        controller._process_frame()
        pump(qt_app, lambda: bool(previews))
        preview, size = previews[0]
        assert size == image.size()
        assert preview.width() <= 1280 and preview.height() <= 720
        assert controller.snapshot().size() == image.size()
        assert controller.snapshot().pixelColor(1599, 1199) == QColor("red")
    finally:
        controller.stop()
        controller.frame_pool.waitForDone()
        qt_app.processEvents()


@pytest.mark.parametrize("conversion_error", [False, True])
def test_slow_conversion_keeps_gui_responsive_and_discards_disconnected_result(
    qt_app, monkeypatch, conversion_error
):
    controller = camera.CameraController()
    image = QImage(30, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    native_frame = QVideoFrame(image)
    entered = threading.Event()
    release = threading.Event()
    threads = []
    delivered = []
    errors = []
    ticks = []
    original = camera._convert_frame

    def slow_conversion(frame):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        if conversion_error:
            raise RuntimeError("Previous connection failed during conversion")
        return original(frame)

    monkeypatch.setattr(camera, "_convert_frame", slow_conversion)
    controller.frame.connect(delivered.append)
    controller.status.connect(errors.append)
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
        assert not errors
        with pytest.raises(ValueError, match="暂无可采集"):
            controller.snapshot()

        monkeypatch.setattr(camera, "_convert_frame", original)
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


def test_mvs_missing_library_leaves_native_inputs_available(qt_app, monkeypatch, tmp_path):
    monkeypatch.setenv("VANNUS_MVS_LIBRARY", str(tmp_path / "missing-mvs-library"))
    controller = camera.CameraController()
    native_device = object()
    monkeypatch.setattr(controller.media, "videoInputs", lambda: [native_device])
    try:
        controller.mvs.refresh()
        pump(qt_app, lambda: bool(controller.mvs.discovery_error))
        assert "MVS SDK" in controller.mvs.discovery_error
        assert controller.devices() == [native_device]
    finally:
        controller.shutdown()


def test_mvs_usb_devices_have_serial_based_ids():
    records = [mvs.UsbDeviceEntry() for _ in range(3)]
    for record, model, serial in zip(records, (mvs.MODEL, mvs.MODEL, "Different"), ("B", "A", "C")):
        record.transport = mvs.USB_DEVICE
        record.usb.model = model.encode()
        record.usb.serial = serial.encode()

    def enum_devices(transport, pointer):
        assert transport == mvs.USB_DEVICE
        devices = pointer._obj
        devices.count = len(records)
        for index, record in enumerate(records):
            devices.entries[index] = ctypes.addressof(record)
        return 0

    sdk = mvs.MvsSdk.__new__(mvs.MvsSdk)
    sdk.EnumDevices = enum_devices
    found = [device for device, _ in sdk.devices()]
    assert [device.id() for device in found] == [b"mvs:usb:B", b"mvs:usb:A"]
    assert len({device.description() for device in found}) == 2


def test_mvs_mono8_snapshot_owns_the_sdk_pixels():
    pixels = (ctypes.c_ubyte * (mvs.WIDTH * mvs.HEIGHT))()
    pixels[13] = 192
    pixels[mvs.WIDTH + 4] = 42
    frame = mvs.FrameOut()
    frame.data = ctypes.addressof(pixels)
    frame.info.width = mvs.WIDTH
    frame.info.height = mvs.HEIGHT
    frame.info.pixel_type = mvs.MONO8
    frame.info.length = len(pixels)

    image = mvs.mono_image(frame)
    pixels[13] = 0
    assert image.size().toTuple() == (1440, 1080)
    assert image.pixelColor(13, 0).getRgb() == (192, 192, 192, 255)
    assert image.pixelColor(4, 1).getRgb() == (42, 42, 42, 255)
    frame.info.length -= 1
    with pytest.raises(mvs.MvsError, match="Mono8"):
        mvs.mono_image(frame)


def test_mvs_applies_manual_settings_and_restores_them_on_exit_or_failure(monkeypatch):
    device = mvs.MvsDevice(mvs.MODEL, "1")
    sdk = Mock()
    for name in (
        "Initialize",
        "CreateHandle",
        "OpenDevice",
        "CloseDevice",
        "Finalize",
        "SetIntValueEx",
        "StartGrabbing",
        "StopGrabbing",
    ):
        getattr(sdk, name).return_value = 0
    sdk.devices.return_value = [(device, 1)]
    sdk.GetImageBuffer.return_value = mvs.NO_DATA
    sdk.is_connected.return_value = True
    modes = {b"ExposureAuto": b"Continuous", b"GainAuto": b"Continuous"}
    controls = {b"ExposureTime": 30_000, b"Gain": 4}
    ranges = {b"ExposureTime": (15, 10_000_000), b"Gain": (0, 17)}
    gamma_enabled = True

    def set_enum(handle, node, value):
        if node in modes:
            modes[node] = value
        return 0

    def get_float(handle, node, pointer):
        assert set(modes.values()) == {b"Off"}
        pointer._obj.current = controls[node]
        pointer._obj.minimum, pointer._obj.maximum = ranges[node]
        return 0

    def set_float(handle, node, value):
        assert set(modes.values()) == {b"Off"}
        if node == b"Gain" and value == 16:
            return 1
        controls[node] = value
        return 0

    def get_bool(handle, node, pointer):
        assert node == b"GammaEnable"
        pointer._obj.value = gamma_enabled
        return 0

    def set_bool(handle, node, value):
        nonlocal gamma_enabled
        if node == b"AcquisitionFrameRateEnable":
            return 1
        assert node == b"GammaEnable"
        gamma_enabled = value
        return 0

    def node_access(handle, node, pointer):
        pointer._obj.value = 4
        return 0

    sdk.SetEnumValueByString.side_effect = set_enum
    sdk.GetFloatValue.side_effect = get_float
    sdk.SetFloatValue.side_effect = set_float
    sdk.GetBoolValue.side_effect = get_bool
    sdk.SetBoolValue.side_effect = set_bool
    sdk.node_access.side_effect = node_access
    monkeypatch.setattr(mvs, "MvsSdk", lambda: sdk)
    backend = mvs.MvsCamera()
    try:
        backend.open(device)
        start = sdk.mock_calls.index(call.StartGrabbing(ANY))
        for node in modes:
            assert sdk.mock_calls.index(call.SetEnumValueByString(ANY, node, b"Off")) < start
        ordinary = mvs.CameraSettings(
            exposure=mvs.FloatControl(30_000, True, 15, 10_000_000),
            gain=mvs.FloatControl(4, True, 0, 17),
            gamma_enabled=True,
        )
        assert backend.settings == ordinary

        _, settings, error = backend.read(mvs.SettingsChange(exposure_us=5_000_000, gain_db=16, candling=True))
        assert settings == ordinary
        assert controls == {b"ExposureTime": 30_000, b"Gain": 4}
        assert "无法设置海康相机增益" in error

        sdk.reset_mock()
        change = mvs.SettingsChange(exposure_us=5_000_000, gain_db=3, candling=True)
        assert backend.read(change) == (
            None,
            mvs.CameraSettings(
                exposure=mvs.FloatControl(5_000_000, True, 15, 10_000_000),
                gain=mvs.FloatControl(3, True, 0, 17),
                gamma_enabled=False,
                candling=True,
            ),
            "",
        )
        assert (
            sdk.mock_calls.index(call.StopGrabbing(ANY))
            < sdk.mock_calls.index(call.SetFloatValue(ANY, b"ExposureTime", 5_000_000))
            < sdk.mock_calls.index(call.StartGrabbing(ANY))
            < sdk.mock_calls.index(call.GetImageBuffer(ANY, ANY, 80))
        )
        assert backend.read(mvs.SettingsChange(candling=False)) == (None, ordinary, "")
        assert gamma_enabled
    finally:
        backend.close()


def test_mvs_waits_for_a_long_exposure_before_reporting_missing_frames(qt_app, monkeypatch):
    stream = mvs_stream.MvsStream()
    device = mvs.MvsDevice(mvs.MODEL, "1")
    settings = mvs.CameraSettings(exposure=mvs.FloatControl(10_000_000, True, 15, 10_000_000))
    current_time = 100.0
    errors = []
    stream.failed.connect(errors.append)

    def submit(work, done=None):
        value = work()
        if done is not None:
            done(value, "")

    monkeypatch.setattr(mvs_stream, "time", SimpleNamespace(monotonic=lambda: current_time))
    monkeypatch.setattr(stream, "_submit", submit)
    monkeypatch.setattr(stream.backend, "open", lambda _: None)
    monkeypatch.setattr(stream.backend, "read", lambda _: (None, settings, ""))
    try:
        stream.open(device)
        current_time += 10
        stream._read()
        assert stream.device == device
        assert errors == []
        current_time += 5
        stream._read()
        assert stream.device is None
        assert len(errors) == 1
    finally:
        stream.stop()


def test_mvs_reconnect_discards_in_flight_frame(qt_app, monkeypatch):
    controller = camera.CameraController()
    devices = [mvs.MvsDevice(mvs.MODEL, serial) for serial in ("1", "2")]
    controller.mvs.devices = devices
    monkeypatch.setattr(controller.media, "videoInputs", lambda: [])
    backend = controller.mvs.backend
    entered, release = threading.Event(), threading.Event()
    opened = []
    delivered = []
    controller.frame.connect(delivered.append)

    def read(change):
        image = QImage(mvs.WIDTH, mvs.HEIGHT, QImage.Format.Format_Grayscale8)
        if opened[-1] == devices[0]:
            image.fill(QColor("white"))
            entered.set()
            assert release.wait(5)
        else:
            image.fill(QColor("black"))
        return (image, time.monotonic()), mvs.CameraSettings(), ""

    monkeypatch.setattr(backend, "open", opened.append)
    monkeypatch.setattr(backend, "read", read)
    try:
        controller.open(0)
        pump(qt_app, entered.is_set)
        controller.open(1)
        with pytest.raises(ValueError, match="暂无可采集"):
            controller.snapshot()
        release.set()
        pump(qt_app, lambda: bool(delivered))
        assert opened == devices
        assert controller.metadata()["device_id"] == devices[1].id().hex()
        assert all(image.pixelColor(0, 0) == QColor("black") for image in delivered)
    finally:
        release.set()
        controller.shutdown()
        qt_app.processEvents()
