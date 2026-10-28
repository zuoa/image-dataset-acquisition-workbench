"""Background work with explicit cancellation and GUI-thread delivery."""

import logging
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .storage import Cancelled


class Signals(QObject):
    progress = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()
        self.cancel = threading.Event()

    @Slot()
    def run(self):
        try:
            result = self.function(self.cancel, self.signals.progress.emit)
            self.signals.succeeded.emit(result)
        except Cancelled as error:
            self.signals.failed.emit(str(error))
        except Exception as error:
            logging.exception("Background task failed")
            self.signals.failed.emit(str(error))
        finally:
            self.signals.finished.emit()


class TaskRunner(QObject):
    progress = Signal(int, int)
    done = Signal(object)
    error = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.job = None

    @property
    def busy(self):
        return self.job is not None

    def start(self, function):
        if self.busy:
            self.error.emit("请等待当前任务完成")
            return False
        self.job = Job(function)
        self.job.signals.progress.connect(self.progress)
        self.job.signals.succeeded.connect(self.done)
        self.job.signals.failed.connect(self.error)
        self.job.signals.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self.pool.start(self.job)
        return True

    @Slot()
    def _finished(self):
        self.job = None
        self.busy_changed.emit(False)

    def cancel(self):
        if self.job:
            self.job.cancel.set()
