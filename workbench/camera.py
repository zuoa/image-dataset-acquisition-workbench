"""System camera adapter. Frames and captures share the same native video stream."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import (
    QCameraPermission,
    QCoreApplication,
    QObject,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage, QTransform
from PySide6.QtMultimedia import (
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaFormat,
    QMediaRecorder,
    QVideoFrame,
    QVideoSink,
)

from .jobs import Job


def _convert_frame(frame):
    image = frame.toImage()
    if image.isNull():
        return image
    # Presentation transforms are not included in toImage(). Keep the preview
    # and saved photo in the same pixel coordinates, without resizing either.
    if frame.rotation().value:
        image = image.transformed(QTransform().rotate(frame.rotation().value))
    if frame.mirrored():
        image = image.mirrored(True, False)
    return image.copy()  # Release the native camera buffer on the worker thread.


class CameraController(QObject):
    frame = Signal(QImage)
    status = Signal(str)
    devices_changed = Signal()
    connected = Signal(bool)
    recording_changed = Signal()
    recording_duration = Signal("qlonglong")
    video_finished = Signal(str, "qlonglong")
    video_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.media = QMediaDevices(self)
        self.media.videoInputsChanged.connect(self._devices_changed)
        self.session = QMediaCaptureSession(self)
        self.sink = None
        self._reset_sink()
        self.frame_pool = QThreadPool(self)
        self.frame_pool.setMaxThreadCount(1)
        self._frame_job = None
        self._frame_job_generation = None
        self._pending_frame = None
        self._stream_generation = 0
        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(42)
        self.frame_timer.timeout.connect(self._process_frame)
        self.recorder = QMediaRecorder(self)
        self.session.setRecorder(self.recorder)
        self.recorder.recorderStateChanged.connect(self._recorder_state)
        self.recorder.durationChanged.connect(self.recording_duration)
        self.recorder.errorOccurred.connect(self._recorder_error)
        self.recording_path = None
        self.recording_stopping = False
        self._recording_error = ""
        self._stop_after_recording = False
        self.camera = None
        self.latest = QImage()
        self.frame_at = 0.0
        self.device_id = None
        self.format = None
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(2000)
        self.watchdog.timeout.connect(self._check_stream)
        self.watchdog.start()

    def devices(self):
        return self.media.videoInputs()

    def _devices_changed(self):
        if self.camera and self.device_id not in [bytes(d.id()) for d in self.devices()]:
            self.stop()
            self.status.emit("摄像头已断开，请重新连接设备")
        self.devices_changed.emit()

    def formats(self, index):
        devices = self.devices()
        if not 0 <= index < len(devices):
            return []
        formats = devices[index].videoFormats()
        return sorted(
            formats,
            key=lambda f: (f.resolution().width() * f.resolution().height(), f.maxFrameRate()),
            reverse=True,
        )

    def open(self, index, format_index=0):
        if self.video_busy:
            self.status.emit("请先停止录制并等待视频保存，再重新连接")
            return
        devices = self.devices()
        if not 0 <= index < len(devices):
            self.status.emit("未发现摄像头，请连接 USB 设备后刷新")
            return
        app = QCoreApplication.instance()
        permission = QCameraPermission()
        state = app.checkPermission(permission)
        if state == Qt.PermissionStatus.Denied:
            self.status.emit("相机权限被拒绝，请在系统隐私设置中允许本应用访问摄像头")
            return
        if state == Qt.PermissionStatus.Undetermined:

            def permitted(result):
                if result.status() == Qt.PermissionStatus.Granted:
                    self.open(index, format_index)
                else:
                    self.status.emit("未获得相机权限，请检查系统隐私设置及应用的相机权限声明")

            app.requestPermission(permission, self, permitted)
            return
        self.stop()
        device = devices[index]
        self.device_id = bytes(device.id())
        self.camera = QCamera(device, self)
        formats = self.formats(index)
        if formats:
            self.format = formats[min(max(0, format_index), len(formats) - 1)]
            self.camera.setCameraFormat(self.format)
        self.camera.errorOccurred.connect(lambda _, message: self._error(message))
        self.camera.activeChanged.connect(self.connected)
        self.session.setCamera(self.camera)
        self.status.emit("正在连接 " + device.description())
        self.frame_timer.start()
        self.camera.start()

    def _error(self, message):
        self.status.emit("摄像头错误：" + message)
        self._stream_generation += 1
        self._pending_frame = None
        self.latest = QImage()
        self.connected.emit(False)
        if self.video_busy:
            self._recording_error = "摄像头错误：" + message
            self.stop_recording()

    @Slot(QVideoFrame)
    def _queue_frame(self, frame):
        if self.camera and frame.isValid():
            self._pending_frame = (QVideoFrame(frame), time.monotonic())

    def _reset_sink(self):
        # A stopped stream can still have native frame events queued for its
        # sink. Give each connection a new receiver so those events cannot be
        # mistaken for frames from the newly opened camera/format.
        self.session.setVideoSink(None)
        if self.sink is not None:
            self.sink.videoFrameChanged.disconnect(self._queue_frame)
            self.sink.deleteLater()
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._queue_frame)
        self.session.setVideoSink(self.sink)

    @Slot()
    def _process_frame(self):
        # Keep just the newest frame and one conversion in flight. Converting
        # every native frame on the GUI thread starves input at high resolutions
        # or frame rates, even when PNG encoding already runs in the background.
        if self._frame_job is not None or self._pending_frame is None:
            return
        frame, received_at = self._pending_frame
        self._pending_frame = None
        generation = self._stream_generation
        self._frame_job = Job(lambda cancel, progress: (generation, received_at, _convert_frame(frame)))
        self._frame_job_generation = generation
        self._frame_job.signals.succeeded.connect(self._frame_ready)
        self._frame_job.signals.failed.connect(self._frame_failed)
        self._frame_job.signals.finished.connect(self._frame_finished)
        self.frame_pool.start(self._frame_job)

    @Slot(object)
    def _frame_ready(self, result):
        generation, received_at, image = result
        if generation != self._stream_generation or image.isNull():
            return
        self.latest = image
        self.frame_at = received_at
        self.frame.emit(image)

    @Slot(str)
    def _frame_failed(self, message):
        if self._frame_job_generation == self._stream_generation:
            self._error(message)

    @Slot()
    def _frame_finished(self):
        self._frame_job = None
        self._frame_job_generation = None

    def _check_stream(self):
        if self.camera and self.camera.isActive() and self.frame_at and time.monotonic() - self.frame_at > 4:
            self.status.emit("未收到新画面，请检查设备或重新连接")
            if self.video_busy:
                self._recording_error = "摄像头画面中断，录制已停止"
                self.stop_recording()

    def snapshot(self):
        if self.latest.isNull() or time.monotonic() - self.frame_at > 2:
            raise ValueError("暂无可采集的新画面，请连接摄像头并等待预览")
        return QImage(self.latest)  # Immutable shared pixels; saving detaches on the worker.

    def metadata(self):
        if not self.camera:
            return {}
        camera = self.camera
        return dict(
            device=camera.cameraDevice().description(),
            device_id=self.device_id.hex(),
            width=self.latest.width(),
            height=self.latest.height(),
            exposure_time=camera.exposureTime(),
            iso=camera.isoSensitivity(),
            white_balance=camera.whiteBalanceMode().name,
            focus=camera.focusMode().name,
            pixel_format=camera.cameraFormat().pixelFormat().name,
        )

    @property
    def video_busy(self):
        # Includes asynchronous encoder finalization after stop() was requested.
        return self.recording_path is not None

    def start_recording(self, path):
        if self.video_busy:
            raise ValueError("当前视频仍在录制或保存")
        if not self.camera or not self.camera.isActive():
            raise ValueError("请先连接摄像头")
        self.snapshot()  # Refuse stale or absent camera frames.
        media_format = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
        media_format.setVideoCodec(QMediaFormat.VideoCodec.H264)
        if not media_format.isSupported(QMediaFormat.ConversionMode.Encode):
            raise ValueError("当前系统不支持 MP4 / H.264 视频编码")
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.recorder.setMediaFormat(media_format)
        self.recorder.setQuality(QMediaRecorder.Quality.HighQuality)
        self.recorder.setVideoResolution(self.camera.cameraFormat().resolution())
        self.recorder.setVideoFrameRate(self.camera.cameraFormat().maxFrameRate())
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(path)))
        self.recording_path = path
        self.recording_stopping = False
        self._recording_error = ""
        self.recording_duration.emit(0)
        self.recording_changed.emit()
        self.recorder.record()

    def stop_recording(self):
        if not self.video_busy or self.recording_stopping:
            return
        self.recording_stopping = True
        self.recording_changed.emit()
        self.recorder.stop()
        if self.recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState:
            QTimer.singleShot(0, self._finish_recording)

    def _recorder_state(self, state):
        if state == QMediaRecorder.RecorderState.StoppedState and self.video_busy:
            # Error signals can follow the state change in the same event cycle.
            QTimer.singleShot(0, self._finish_recording)

    def _recorder_error(self, _, message):
        if self.video_busy:
            self._recording_error = message or "视频编码失败"
            self.stop_recording()

    def _finish_recording(self):
        if not self.video_busy or self.recorder.recorderState() != QMediaRecorder.RecorderState.StoppedState:
            return
        path = self.recording_path
        actual = self.recorder.actualLocation().toLocalFile()
        if actual:
            path = Path(actual)
        duration = self.recorder.duration()
        error = self._recording_error
        self.recording_path = None
        self.recording_stopping = False
        if self._stop_after_recording:
            self._stop_after_recording = False
            self.stop()
        self.recording_changed.emit()
        if error or not path.is_file() or path.stat().st_size == 0 or duration <= 0:
            self.video_error.emit(error or "未录到有效视频，请等待画面后重试")
        else:
            self.video_finished.emit(str(path), duration)

    def stop(self):
        if self.video_busy:
            self._stop_after_recording = True
            self.stop_recording()
            return
        self.frame_timer.stop()
        self._stream_generation += 1
        self._pending_frame = None
        self.latest = QImage()
        self.frame_at = 0
        self.format = None
        if self.camera:
            self.camera.stop()
            self.session.setCamera(None)
            self.camera.deleteLater()
            self.camera = None
        self._reset_sink()
        self.connected.emit(False)
