"""Qt-facing MVS stream with one SDK operation in flight per acquisition tick."""

import logging
import time

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, Signal, Slot

from .jobs import Job
from .mvs import CameraSettings, MvsCamera, MvsError


class MvsStream(QObject):
    frame = Signal(object, float)
    connected = Signal(bool)
    failed = Signal(str)
    settings_changed = Signal()
    settings_error = Signal(str)
    devices_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend = MvsCamera()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(34)
        self.timer.timeout.connect(self._read)
        self.devices = []
        self.discovery_error = ""
        self.sdk_ready = None
        self.device = None
        self.settings = CameraSettings()
        self.settings_pending = False
        self.settings_frame_at = 0.0
        self.exposure_wait = 0.0
        self.active = False
        self._generation = 0
        self._received_at = 0.0
        self._discovering = False
        self._reading = False
        self._settings_change = None

    def _submit(self, work, done=None):
        def run(cancel, progress):
            try:
                return done, work(), ""
            except MvsError as error:
                return done, None, str(error)
            except Exception as error:
                logging.exception("MVS operation failed")
                return done, None, str(error)

        job = Job(run)
        job.signals.succeeded.connect(self._deliver)
        self.pool.start(job)

    @Slot(object)
    def _deliver(self, result):
        done, value, error = result
        if done is not None:
            done(value, error)

    def refresh(self):
        if self._discovering:
            return
        self._discovering = True
        self._submit(self.backend.devices, self._refreshed)

    def _refreshed(self, devices, error):
        self._discovering = False
        self.devices = devices or []
        self.discovery_error = error
        self.sdk_ready = not bool(error)
        self.devices_changed.emit()

    def open(self, device):
        self.device = device
        generation = self._generation

        def opened(_, error):
            if generation != self._generation:
                return
            if error:
                self._fail(error)
                return
            self._received_at = time.monotonic()
            self.timer.start()
            self._read()

        self._submit(lambda: self.backend.open(device), opened)

    def _read(self):
        if self._reading:
            return
        self._reading = True
        generation = self._generation
        change = self._settings_change
        self._settings_change = None

        def ready(result, error):
            self._reading = False
            if generation != self._generation:
                return
            if error:
                self._fail(error)
                return
            if self._settings_change is not None:
                return
            packet, self.settings, settings_error = result
            exposure_seconds = (self.settings.exposure.value or 0) / 1_000_000
            if change is not None:
                self._received_at = time.monotonic()
                self.exposure_wait = exposure_seconds
            if packet is not None:
                self.exposure_wait = exposure_seconds
                image, self._received_at = packet
                adjusted = self.settings_pending
                if adjusted:
                    self.settings_pending = False
                    self.settings_frame_at = self._received_at
                self.frame.emit(image, self._received_at)
                if not self.active:
                    self.active = True
                    self.connected.emit(True)
                if adjusted:
                    self.settings_changed.emit()
            else:
                self.exposure_wait = max(self.exposure_wait, exposure_seconds)
                if time.monotonic() - self._received_at > 4 + self.exposure_wait:
                    self._fail("未收到海康相机画面，请检查 USB 连接并重连")
                    return
            if settings_error:
                self.settings_error.emit(settings_error)

        self._submit(lambda: self.backend.read(change), ready)

    def adjust(self, change):
        if self.active and not self.settings_pending:
            self._settings_change = change
            self.settings_pending = True
            self.settings_changed.emit()

    def _fail(self, message):
        self.stop()
        self.failed.emit(message)

    def stop(self):
        if self.device is None:
            return
        self.timer.stop()
        self._generation += 1
        self.device = None
        self.active = False
        self.settings = CameraSettings()
        self.settings_pending = False
        self.settings_frame_at = 0.0
        self.exposure_wait = 0.0
        self._settings_change = None
        self._submit(self.backend.close)
