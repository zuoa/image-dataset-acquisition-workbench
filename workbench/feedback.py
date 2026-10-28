"""Local, non-blocking completion feedback shared by photo and video capture."""

from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QSoundEffect


class CaptureFeedback(QObject):
    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        self.preferences = preferences
        self.sound = QSoundEffect(self)
        self.sound.setSource(
            QUrl.fromLocalFile(str(Path(__file__).parent / "assets" / "capture-complete.wav"))
        )
        self.sound.setVolume(0.55)
        self.sound.setLoopCount(1)

    @property
    def enabled(self):
        return self.preferences.value("capture/sound_enabled", True, type=bool)

    def set_enabled(self, enabled):
        self.preferences.setValue("capture/sound_enabled", bool(enabled))
        if not enabled:
            self.sound.stop()

    def play(self):
        if self.enabled:
            self.preview()

    def preview(self):
        self.sound.stop()
        self.sound.play()
