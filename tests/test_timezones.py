from datetime import datetime

import pytest
from PIL import Image
from PySide6.QtCore import QLockFile, QSettings
from PySide6.QtWidgets import QLabel

from workbench.app import MainWindow
from workbench.storage import Workspace
from workbench.timeutils import format_datetime
from workbench.ui.library import PhotoDialog


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("2026-11-03T01:02:03.456+00:00", "2026-11-03 09:02:03"),
        ("2026-10-31T16:00:00Z", "2026-11-01 00:00:00"),
        ("2026-12-31T23:59:59+00:00", "2027-01-01 07:59:59"),
        ("2026-11-03T09:02:03+08:00", "2026-11-03 09:02:03"),
        ("2026-11-02T17:02:03-08:00", "2026-11-03 09:02:03"),
        ("2026-11-03T01:02:03", "2026-11-03 09:02:03"),
    ],
)
def test_archive_times_display_in_utc_plus_eight(stored, expected):
    assert format_datetime(stored) == expected + " UTC+08:00"


def test_native_time_displays_and_today_count_share_east_eight_midnight(qt_app, tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "timezone-workspace")
    dataset = workspace.save_dataset("东八区日期边界")
    batch = workspace.save_batch(dataset, "跨日采集")
    timestamps = [
        "2026-11-02T15:59:59.999+00:00",
        "2026-11-02T16:00:00.000+00:00",
        "2026-11-02T16:00:01.000+00:00",
    ]
    photo_ids = []
    for timestamp in timestamps:
        monkeypatch.setattr("workbench.storage.now", lambda value=timestamp: value)
        photo_ids.append(
            workspace.add_photo(
                Image.new("RGB", (12, 8), "teal"),
                dataset,
                batch,
                regions=[dict(name="热区", x=0, y=0, w=6, h=4)],
            )
        )
    source = workspace.new_recording_path()
    source.write_bytes(b"archive fixture, not used for playback")
    video_id = workspace.add_video(source, dataset, batch, duration_ms=1000)
    monkeypatch.setattr(
        "workbench.ui.home.display_now", lambda: datetime.fromisoformat("2026-11-03T00:05:00+08:00")
    )
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    preferences = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    window = MainWindow(workspace, preferences, tmp_path, lock)
    dialog = None
    try:
        window.show()
        qt_app.processEvents()
        assert qt_app.platformName() not in {"offscreen", "minimal"}
        assert window.home.metrics[2].value_label.text() == "2"
        window.open_library(dataset)
        for item_id in (photo_ids[-1], video_id):
            index = next(i for i, row in enumerate(window.library.model.rows) if row["id"] == item_id)
            window.library.view.setCurrentIndex(window.library.model.index(index))
            qt_app.processEvents()
            assert "2026-11-03 00:00:01 UTC+08:00" in window.library.selection_details.text()

        photo = workspace.photo(photo_ids[-1])
        assert photo["created_at"] == timestamps[-1]
        dialog = PhotoDialog(window, photo)
        dialog.show()
        qt_app.processEvents()
        fields = [label.text() for label in dialog.findChildren(QLabel)]
        assert any("2026-11-03 00:00:01 UTC+08:00" in text for text in fields)
        assert any("处理 v1" in text and "2026-11-03 00:00:01 UTC+08:00" in text for text in fields)
        dialog.close()

        workspace.trash("photo", [photo_ids[-1]])
        window.trash.refresh()
        assert "2026-11-03 00:00:01 UTC+08:00" in window.trash.list.item(0).text()
    finally:
        if dialog:
            dialog.close()
        window.close()
        qt_app.processEvents()
