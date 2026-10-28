"""Archive byte preservation and recovery; these tests do not simulate a camera."""

import json
import sqlite3
import threading
import zipfile

import pytest

from workbench.storage import Cancelled, Workspace


@pytest.fixture
def video_archive(tmp_path):
    workspace = Workspace(tmp_path / "视频工作区")
    dataset = workspace.save_dataset("实验视频")
    batch = workspace.save_batch(dataset, "第一批")
    source = workspace.new_recording_path()
    # Media is opaque to storage: only the native recorder validates its encoding.
    content = b"video archive byte preservation fixture\x00" * 50000
    source.write_bytes(content)
    video_id = workspace.add_video(
        source,
        dataset,
        batch,
        duration_ms=3200,
        sample_id="V-001",
        tags="自然光",
        note="录制实验",
        metadata={"custom": {"温度": "25°C"}, "camera": {"device": "USB"}},
    )
    return workspace, dataset, batch, video_id, content


def test_video_dataset_roundtrip_and_backup_preserve_media_and_metadata(video_archive, tmp_path):
    workspace, dataset, _, video_id, content = video_archive
    archive = tmp_path / "数据集.zip"
    assert workspace.export_zip(archive, dataset) == 1
    restored_id = workspace.import_zip(archive)
    video = workspace.videos(dataset_id=restored_id)[0]
    assert video["id"] != video_id
    assert video["sample_id"] == "V-001"
    assert video["duration_ms"] == 3200
    assert json.loads(video["metadata"])["custom"] == {"温度": "25°C"}
    assert workspace.path(video["original"]).read_bytes() == content
    backup = tmp_path / "工作区.zip"
    workspace.backup(backup)
    destination = tmp_path / "恢复工作区"
    Workspace.restore_backup(backup, destination)
    restored = Workspace(destination)
    assert len(restored.videos()) == 2
    assert all(restored.path(row["original"]).read_bytes() == content for row in restored.videos())


def test_parent_deletion_restore_and_empty_trash_include_videos(video_archive):
    workspace, dataset, batch, video_id, _ = video_archive
    path = workspace.path(workspace.videos()[0]["original"])
    workspace.trash("dataset", [dataset])
    workspace.trash("batch", [batch])
    workspace.trash("video", [video_id])
    assert not workspace.videos()
    workspace.restore("video", video_id)
    assert len(workspace.videos()) == 1
    workspace.trash("dataset", [dataset])
    workspace.empty_trash()
    assert not path.exists()
    assert not workspace.trash_items()


def test_failed_video_commit_returns_file_to_staging(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    dataset = workspace.save_dataset("实验")
    batch = workspace.save_batch(dataset, "批次")
    source = workspace.new_recording_path()
    source.write_bytes(b"archive contents")
    with workspace.connect() as db:
        db.execute("""CREATE TRIGGER reject_video BEFORE INSERT ON videos
            BEGIN SELECT RAISE(ABORT, 'disk write rejected'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        workspace.add_video(source, dataset, batch, duration_ms=1000)
    assert source.read_bytes() == b"archive contents"
    assert not workspace.videos()
    assert not list((workspace.root / "videos").iterdir())


def test_video_checksum_failure_rolls_back_import(video_archive, tmp_path):
    workspace, dataset, _, _, _ = video_archive
    archive = tmp_path / "good.zip"
    workspace.export_zip(archive, dataset)
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupt, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"damaged" if name.endswith(".mp4") else source.read(name))
    with pytest.raises(ValueError, match="视频校验失败"):
        workspace.import_zip(corrupt)
    assert len(workspace.datasets()) == 1
    assert len(workspace.videos()) == 1
    assert not list((workspace.root / ".staging").iterdir())


def test_cancel_video_export_preserves_destination(video_archive, tmp_path):
    workspace, dataset, _, _, _ = video_archive
    destination = tmp_path / "archive.zip"
    destination.write_bytes(b"existing destination")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        workspace.export_zip(destination, dataset, cancel=cancel)
    assert destination.read_bytes() == b"existing destination"
    assert not list(tmp_path.glob("*.partial"))


def test_existing_schema_and_backups_upgrade_without_losing_photos(tmp_path):
    from PIL import Image

    workspace = Workspace(tmp_path / "legacy")
    dataset = workspace.save_dataset("旧工作区")
    batch = workspace.save_batch(dataset, "批次")
    workspace.add_photo(Image.new("RGB", (8, 8), "teal"), dataset, batch)
    with workspace.connect() as db:
        db.execute("DROP TABLE videos")
        db.execute("PRAGMA user_version=1")
    backup = tmp_path / "legacy.zip"
    workspace.backup(backup)
    destination = tmp_path / "restored"
    Workspace.restore_backup(backup, destination)
    restored = Workspace(destination)
    assert restored.count_photos() == 1
    assert restored.videos() == []
