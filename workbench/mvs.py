"""Small binding to Hikrobot's locally installed MvCameraControl C SDK."""

from __future__ import annotations

import ctypes as C
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage

MODEL = "MV-CS016-10UM"
WIDTH, HEIGHT = 1440, 1080
VIDEO_FPS = 30
USB_DEVICE = 4  # MV_USB_DEVICE
MONO8 = 0x01080001  # PixelType_Gvsp_Mono8
NO_DATA = 0x80000007  # MV_E_NODATA
USB_DRIVER_ERROR = 0x80000305  # MV_E_USB_DRIVER


class MvsError(RuntimeError):
    def __init__(self, message, code=0):
        self.code = code
        super().__init__(f"{message}（MVS 0x{code:08X}）" if code else message)


@dataclass(frozen=True)
class MvsDevice:
    model: str
    serial: str

    def id(self):
        return b"mvs:usb:" + self.serial.encode("utf-8")

    def description(self):
        return f"海康 {self.model} · {self.serial}"


@dataclass(frozen=True)
class FloatControl:
    value: float | None = None
    writable: bool = False
    minimum: float = 0.0
    maximum: float = 0.0

    def clamp(self, value):
        return min(self.maximum, max(self.minimum, value))


@dataclass(frozen=True)
class CameraSettings:
    exposure: FloatControl = FloatControl()
    gain: FloatControl = FloatControl()
    gamma_enabled: bool | None = None
    candling: bool = False

    @property
    def supports_candling(self):
        return self.exposure.writable and self.gain.writable


@dataclass(frozen=True)
class SettingsChange:
    exposure_us: float | None = None
    gain_db: float | None = None
    candling: bool | None = None


# Layouts from the MVS 4.x CameraParams.h. We read only the USB member of
# MV_CC_DEVICE_INFO and pass its original, SDK-owned address to CreateHandle.
class UsbDeviceInfo(C.Structure):
    _fields_ = [
        ("endpoints", C.c_ubyte * 4),
        ("vendor_id", C.c_uint16),
        ("product_id", C.c_uint16),
        ("device_number", C.c_uint32),
        ("guid", C.c_char * 64),
        ("vendor", C.c_char * 64),
        ("model", C.c_char * 64),
        ("family", C.c_char * 64),
        ("version", C.c_char * 64),
        ("manufacturer", C.c_char * 64),
        ("serial", C.c_char * 64),
        ("user_name", C.c_char * 64),
        ("usb_version", C.c_uint32),
        ("address", C.c_uint32),
        ("reserved", C.c_uint32 * 2),
    ]


class UsbDeviceEntry(C.Structure):
    _fields_ = [
        ("major", C.c_uint16),
        ("minor", C.c_uint16),
        ("mac", C.c_uint32 * 2),
        ("transport", C.c_uint32),
        ("device_type_and_reserved", C.c_uint32 * 4),
        ("usb", UsbDeviceInfo),
    ]


class DeviceList(C.Structure):
    _fields_ = [("count", C.c_uint32), ("entries", C.c_void_p * 256)]


class FrameInfo(C.Structure):
    _fields_ = [
        ("width", C.c_uint16),
        ("height", C.c_uint16),
        # The macOS header combines -1 with unsigned custom pixel values.
        # Clang uses an 8-byte enum for that range. The Windows enum is 4 bytes.
        ("pixel_type", C.c_uint64 if sys.platform == "darwin" else C.c_uint32),
        ("frame_number", C.c_uint32),
        ("device_timestamp", C.c_uint32 * 2),
        ("reserved_0", C.c_uint32),
        ("host_timestamp", C.c_int64),
        ("length", C.c_uint32),
        ("second_count", C.c_uint32),
        ("cycle_count", C.c_uint32),
        ("cycle_offset", C.c_uint32),
        ("gain", C.c_float),
        ("exposure", C.c_float),
        ("average_brightness", C.c_uint32),
        ("white_balance", C.c_uint32 * 3),
        ("frame_counter", C.c_uint32),
        ("trigger_index", C.c_uint32),
        ("input", C.c_uint32),
        ("output", C.c_uint32),
        ("roi", C.c_uint16 * 4),
        ("lost_packets", C.c_uint32),
        ("unparsed_chunk_count", C.c_uint32),
        ("unparsed_chunk_list", C.c_uint64),
        ("extended_width", C.c_uint32),
        ("extended_height", C.c_uint32),
        ("extended_length", C.c_uint64),
        ("extra_type", C.c_uint32),
        ("subimage_count", C.c_uint32),
        ("subimage_list", C.c_uint64),
        ("user_ptr", C.c_uint64),
        ("first_line_encoder_count", C.c_uint32),
        ("last_line_encoder_count", C.c_uint32),
        ("reserved", C.c_uint32 * 24),
    ]


class FrameOut(C.Structure):
    _fields_ = [("data", C.c_void_p), ("info", FrameInfo), ("reserved", C.c_uint32 * 16)]


class FloatValue(C.Structure):
    _fields_ = [
        ("current", C.c_float),
        ("maximum", C.c_float),
        ("minimum", C.c_float),
        ("reserved", C.c_uint32 * 4),
    ]


def _library_path():
    override = os.environ.get("VANNUS_MVS_LIBRARY")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path("/Library/Frameworks/MvCameraControl.framework/MvCameraControl")
    if sys.platform == "win32":
        for variable in ("CommonProgramFiles(x86)", "CommonProgramW6432", "CommonProgramFiles"):
            root = os.environ.get(variable)
            if root:
                path = Path(root) / "MVS" / "Runtime" / "Win64_x64" / "MvCameraControl.dll"
                if path.is_file():
                    return path
        return Path("MvCameraControl.dll")
    raise MvsError("当前系统未提供海康 MVS 接入")


def _check(code, message):
    if code:
        raise MvsError(message, code & 0xFFFFFFFF)


class MvsSdk:
    def __init__(self):
        path = _library_path()
        self.dll_directory = None
        try:
            if sys.platform == "win32":
                if path.is_absolute():
                    self.dll_directory = os.add_dll_directory(str(path.parent))
                self.lib = C.WinDLL(str(path))
            else:
                self.lib = C.CDLL(str(path))
            self._bind()
        except (OSError, AttributeError) as error:
            self.close()
            raise MvsError("无法加载海康 MVS SDK，请安装匹配系统架构的 MVS 和 USB3 Vision 驱动") from error

    def _bind(self):
        handle, key, uint, ptr = C.c_void_p, C.c_char_p, C.c_uint32, C.POINTER
        signatures = {
            "Initialize": (),
            "Finalize": (),
            "EnumDevices": (uint, ptr(DeviceList)),
            "CreateHandle": (ptr(handle), handle),
            "OpenDevice": (handle, uint, C.c_uint16),
            "CloseDevice": (handle,),
            "DestroyHandle": (handle,),
            "StartGrabbing": (handle,),
            "StopGrabbing": (handle,),
            "GetImageBuffer": (handle, ptr(FrameOut), uint),
            "FreeImageBuffer": (handle, ptr(FrameOut)),
            "SetEnumValueByString": (handle, key, key),
            "SetIntValueEx": (handle, key, C.c_int64),
            "GetFloatValue": (handle, key, ptr(FloatValue)),
            "SetFloatValue": (handle, key, C.c_float),
            "GetBoolValue": (handle, key, ptr(C.c_bool)),
            "SetBoolValue": (handle, key, C.c_bool),
        }
        for name, args in signatures.items():
            function = getattr(self.lib, "MV_CC_" + name)
            function.argtypes = args
            function.restype = C.c_int
            setattr(self, name, function)
        self.is_connected = self.lib.MV_CC_IsDeviceConnected
        self.is_connected.argtypes = (handle,)
        self.is_connected.restype = C.c_bool
        self.node_access = self.lib.MV_XML_GetNodeAccessMode
        self.node_access.argtypes = (handle, key, ptr(C.c_int))
        self.node_access.restype = C.c_int

    def close(self):
        if self.dll_directory is not None:
            self.dll_directory.close()
            self.dll_directory = None

    def devices(self):
        devices = DeviceList()
        _check(self.EnumDevices(USB_DEVICE, C.byref(devices)), "无法枚举海康 USB3 Vision 设备")
        for address in devices.entries[: devices.count]:
            if not address:
                continue
            entry = UsbDeviceEntry.from_address(address)
            if entry.transport != USB_DEVICE:
                continue
            model = entry.usb.model.decode("utf-8", errors="replace").strip()
            serial = (entry.usb.serial or entry.usb.guid).decode("utf-8", errors="replace").strip()
            if model in (MODEL, MODEL + " V5") and serial:
                yield MvsDevice(model, serial), address


def mono_image(frame):
    info = frame.info
    if (
        info.pixel_type != MONO8
        or (info.width, info.height) != (WIDTH, HEIGHT)
        or info.length < WIDTH * HEIGHT
        or not frame.data
    ):
        raise MvsError("海康相机返回的画面不符合全幅 Mono8 格式，请检查相机配置")
    pixels = (C.c_ubyte * (WIDTH * HEIGHT)).from_address(frame.data)
    return QImage(memoryview(pixels), WIDTH, HEIGHT, WIDTH, QImage.Format.Format_Grayscale8).copy()


class MvsCamera:
    """Synchronous SDK operations, serialized by the stream's worker pool."""

    def __init__(self):
        self.sdk = None
        self.handle = C.c_void_p()
        self.opened = False
        self.grabbing = False
        self.settings = CameraSettings()
        self._manual_gain = False
        self._normal_settings = None

    def _initialize(self):
        sdk = MvsSdk()
        try:
            _check(sdk.Initialize(), "海康 MVS SDK 初始化失败")
        except MvsError:
            sdk.close()
            raise
        self.sdk = sdk

    def devices(self):
        temporary = self.sdk is None
        if temporary:
            self._initialize()
        try:
            return [device for device, _ in self.sdk.devices()]
        finally:
            if temporary:
                self.close()

    def open(self, device):
        self._initialize()
        sdk, handle = self.sdk, self.handle
        try:
            address = next((address for found, address in sdk.devices() if found.id() == device.id()), None)
            if address is None:
                raise MvsError("海康相机已断开，请连接设备后刷新")
            _check(sdk.CreateHandle(C.byref(handle), address), "无法创建海康相机连接")
            code = sdk.OpenDevice(handle, 1, 0)  # MV_ACCESS_Exclusive
            if code & 0xFFFFFFFF == USB_DRIVER_ERROR:
                _check(code, "无法打开海康相机，请检查 MVS USB3 Vision 驱动")
            _check(code, "无法打开海康相机，请关闭占用相机的 MVS 客户端或其他应用并检查 USB 连接")
            self.opened = True
            for node, value in (
                (b"TriggerMode", b"Off"),
                (b"AcquisitionMode", b"Continuous"),
                (b"PixelFormat", b"Mono8"),
            ):
                _check(sdk.SetEnumValueByString(handle, node, value), f"无法设置海康相机 {node.decode()}")
            for node, value in ((b"OffsetX", 0), (b"OffsetY", 0), (b"Width", WIDTH), (b"Height", HEIGHT)):
                _check(sdk.SetIntValueEx(handle, node, value), f"无法设置海康相机 {node.decode()}")
            self._limit_frame_rate()
            _check(
                sdk.SetEnumValueByString(handle, b"ExposureAuto", b"Off"),
                "无法关闭海康相机自动曝光",
            )
            self._manual_gain = not sdk.SetEnumValueByString(handle, b"GainAuto", b"Off")
            self.settings = self._read_settings()
            self._start_grabbing()
        except Exception:
            self.close()
            raise

    def _start_grabbing(self):
        _check(self.sdk.StartGrabbing(self.handle), "海康相机启动取流失败")
        self.grabbing = True

    def _limit_frame_rate(self):
        sdk, handle = self.sdk, self.handle
        node = b"AcquisitionFrameRate"
        if sdk.SetBoolValue(handle, b"AcquisitionFrameRateEnable", True):
            return
        rate = FloatValue()
        if not sdk.GetFloatValue(handle, node, C.byref(rate)):
            sdk.SetFloatValue(handle, node, min(rate.maximum, max(rate.minimum, VIDEO_FPS)))

    def _writable(self, node):
        access = C.c_int()
        return not self.sdk.node_access(self.handle, node, C.byref(access)) and access.value in (2, 4)

    def _read_float(self, node, error_message=None):
        value = FloatValue()
        code = self.sdk.GetFloatValue(self.handle, node, C.byref(value))
        if code:
            if error_message:
                _check(code, error_message)
            return FloatControl()
        return FloatControl(value.current, self._writable(node), value.minimum, value.maximum)

    def _read_settings(self, candling=False):
        gamma = C.c_bool()
        has_gamma = self._writable(b"GammaEnable") and not self.sdk.GetBoolValue(
            self.handle, b"GammaEnable", C.byref(gamma)
        )
        return CameraSettings(
            exposure=self._read_float(b"ExposureTime", "无法读取海康相机曝光时间"),
            gain=self._read_float(b"Gain") if self._manual_gain else FloatControl(),
            gamma_enabled=gamma.value if has_gamma else None,
            candling=candling,
        )

    def _apply_settings(self, change):
        current = self.settings
        normal = self._normal_settings
        exposure, gain, gamma = change.exposure_us, change.gain_db, None
        if change.candling is True:
            if not current.supports_candling:
                return "当前海康相机无法手动设置曝光和增益"
            normal = normal or current
            gain = 0.0 if gain is None else gain
            gamma = False if current.gamma_enabled is not None else None
        elif change.candling is False and normal is not None:
            exposure, gain, gamma = normal.exposure.value, normal.gain.value, normal.gamma_enabled
            normal = None

        sdk, handle = self.sdk, self.handle
        writes = []
        try:
            for node, value, control, label in (
                (b"ExposureTime", exposure, current.exposure, "曝光时间"),
                (b"Gain", gain, current.gain, "增益"),
            ):
                if value is None:
                    continue
                if not control.writable:
                    raise MvsError(f"当前海康相机无法设置{label}")
                _check(sdk.SetFloatValue(handle, node, control.clamp(value)), f"无法设置海康相机{label}")
                writes.append((sdk.SetFloatValue, node, control.value))
            if gamma is not None and gamma != current.gamma_enabled:
                _check(sdk.SetBoolValue(handle, b"GammaEnable", gamma), "无法设置海康相机 Gamma")
                writes.append((sdk.SetBoolValue, b"GammaEnable", current.gamma_enabled))
            updated = self._read_settings(candling=normal is not None)
            if gain is not None and updated.gain.value is None:
                raise MvsError("无法读取海康相机增益")
            if gamma is not None and updated.gamma_enabled != gamma:
                raise MvsError("无法确认海康相机 Gamma 设置")
        except MvsError as error:
            for setter, node, value in reversed(writes):
                _check(setter(handle, node, value), "无法恢复海康相机参数，请重新连接")
            return str(error)

        self.settings = updated
        self._normal_settings = normal
        return ""

    def read(self, change):
        error = ""
        if change is not None:
            _check(self.sdk.StopGrabbing(self.handle), "无法暂停海康相机取流以更新参数")
            self.grabbing = False
            error = self._apply_settings(change)
            self._start_grabbing()
        frame = FrameOut()
        code = self.sdk.GetImageBuffer(self.handle, C.byref(frame), 80)
        if code and not self.sdk.is_connected(self.handle):
            raise MvsError("海康相机已断开，请连接设备后刷新并重连")
        if code & 0xFFFFFFFF == NO_DATA:
            return None, self.settings, error
        _check(code, "海康相机取流失败，请检查 USB 连接")
        try:
            image = mono_image(frame)
        finally:
            _check(self.sdk.FreeImageBuffer(self.handle, C.byref(frame)), "无法释放海康图像缓冲区")
        return (image, time.monotonic()), self.settings, error

    def close(self):
        if self.sdk is None:
            return
        if self.grabbing:
            self.sdk.StopGrabbing(self.handle)
        if self._normal_settings is not None and self.sdk.is_connected(self.handle):
            try:
                error = self._apply_settings(SettingsChange(candling=False))
                if error:
                    logging.warning("%s", error)
            except MvsError:
                logging.exception("无法恢复海康相机参数")
        if self.opened:
            self.sdk.CloseDevice(self.handle)
        if self.handle:
            self.sdk.DestroyHandle(self.handle)
        self.sdk.Finalize()
        self.sdk.close()
        self.handle = C.c_void_p()
        self.opened = self.grabbing = False
        self.settings = CameraSettings()
        self._normal_settings = None
        self.sdk = None
