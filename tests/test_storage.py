import hashlib
import json
import threading
import zipfile

import pytest
from PIL import Image

from workbench.storage import Cancelled, Workspace


@pytest.fixture
def archive(tmp_path):
    workspace = Workspace(tmp_path / "中文实验")
    dataset = workspace.save_dataset("番茄表面", "成熟度实验", "实验,室内")
    batch = workspace.save_batch(dataset, "第一批", {"note": "自然光"})
    template = workspace.save_template(
        dataset, "中心热区", 120, 80, [dict(name="样本", x=10, y=12, w=30, h=20)]
    )
    image = Image.new("RGB", (120, 80), (30, 90, 160))
    image.paste((220, 50, 30), (10, 12, 40, 32))
    photo = workspace.add_photo(
        image,
        dataset,
        batch,
        sample_id="TOM-001",
        tags="红色",
        note="第一次",
        custom={"温度": "25°C"},
        regions=template["regions"],
        template=template,
    )
    return workspace, dataset, batch, template, photo


def test_capture_is_lossless_and_crop_coordinates_match(archive):
    workspace, _, _, _, photo_id = archive
    photo = workspace.photo(photo_id)
    original = Image.open(workspace.path(photo["original"]))
    processing = json.loads(photo["processing"])
    crop = Image.open(workspace.path(processing[0]["crops"][0]["path"]))
    assert original.size == (120, 80)
    assert crop.size == (30, 20)
    assert crop.getpixel((0, 0)) == (220, 50, 30)
    assert original.getpixel((0, 0)) == (30, 90, 160)


def test_invalid_crop_rolls_back_all_files_and_database(archive):
    workspace, dataset, batch, _, _ = archive
    with pytest.raises(ValueError):
        workspace.add_photo(
            Image.new("RGB", (120, 80)), dataset, batch, regions=[dict(name="bad", x=115, y=0, w=30, h=20)]
        )
    assert workspace.count_photos() == 1
    assert len(list((workspace.root / "images").iterdir())) == 1
    assert not list((workspace.root / ".staging").iterdir())


def test_reprocessing_preserves_original_and_history(archive):
    workspace, dataset, _, template, photo_id = archive
    path = workspace.path(workspace.photo(photo_id)["original"])
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    template2 = workspace.save_template(
        dataset, template["name"], 120, 80, [dict(name="新区域", x=0, y=0, w=10, h=10)]
    )
    assert template2["version"] == 2
    workspace.reprocess(photo_id, template2)
    history = json.loads(workspace.photo(photo_id)["processing"])
    assert len(history) == 2
    assert all(workspace.path(c["path"]).exists() for p in history for c in p["crops"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum
    with pytest.raises(ValueError):
        workspace.reprocess(photo_id, dict(template2, width=121))


def test_full_export_import_preserves_metadata_and_versions(archive, tmp_path):
    workspace, dataset, _, template, photo_id = archive
    workspace.reprocess(photo_id, template)
    destination = tmp_path / "实验.zip"
    assert workspace.export_zip(destination, dataset) == 1
    with zipfile.ZipFile(destination) as z:
        assert "metadata.csv" in z.namelist()
        manifest = json.loads(z.read("manifest.json"))
        assert len(manifest["files"]) == 3
        assert any(name.startswith("originals/") for name in z.namelist())
        assert any(name.startswith("crops/") for name in z.namelist())
    restored = workspace.import_zip(destination)
    assert restored != dataset
    new = workspace.photos(dataset_id=restored)[0]
    assert new["id"] != photo_id
    assert new["sample_id"] == "TOM-001"
    assert json.loads(new["metadata"])["custom"] == {"温度": "25°C"}
    assert len(json.loads(new["processing"])) == 2
    assert (
        workspace.path(new["original"]).read_bytes()
        == workspace.path(workspace.photo(photo_id)["original"]).read_bytes()
    )


def test_corrupt_import_is_atomic(archive, tmp_path):
    workspace, dataset, _, _, _ = archive
    good, bad = tmp_path / "good.zip", tmp_path / "bad.zip"
    workspace.export_zip(good, dataset)
    with zipfile.ZipFile(good) as z, zipfile.ZipFile(bad, "w") as output:
        for name in z.namelist():
            output.writestr(name, b"broken" if name.startswith("originals/") else z.read(name))
    with pytest.raises(ValueError, match="校验失败"):
        workspace.import_zip(bad)
    assert len(workspace.datasets()) == 1
    assert len(list((workspace.root / "images").iterdir())) == 1
    assert not list((workspace.root / ".staging").iterdir())


def test_crop_only_package_cannot_be_restored(archive, tmp_path):
    workspace, dataset, _, _, _ = archive
    output = tmp_path / "crop.zip"
    workspace.export_zip(output, dataset, crops_only=True)
    with pytest.raises(ValueError, match="仅包含裁剪图"):
        workspace.import_zip(output)


def test_export_cancel_preserves_existing_destination(archive, tmp_path):
    workspace, dataset, _, _, _ = archive
    output = tmp_path / "archive.zip"
    output.write_bytes(b"previous archive")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        workspace.export_zip(output, dataset, cancel=cancel)
    assert output.read_bytes() == b"previous archive"
    assert not list(tmp_path.glob("*.partial"))


def test_reject_zip_traversal(archive, tmp_path):
    workspace, *_ = archive
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escape.png", "bad")
    with pytest.raises(ValueError, match="不安全"):
        workspace.import_zip(bad)
    assert not (tmp_path / "escape.png").exists()


def test_parent_trash_restore_does_not_restore_individually_deleted_photo(archive):
    workspace, dataset, batch, _, photo = archive
    workspace.trash("photo", [photo])
    workspace.trash("dataset", [dataset])
    workspace.restore("dataset", dataset)
    assert workspace.count_photos(dataset_id=dataset) == 0
    workspace.restore("photo", photo)
    assert workspace.count_photos(dataset_id=dataset) == 1
    workspace.trash("batch", [batch])
    assert workspace.count_photos() == 0
    workspace.restore("photo", photo)
    assert workspace.count_photos() == 1


def test_clear_trash_removes_children_but_keeps_moved_photos(archive):
    workspace, dataset, batch, _, photo = archive
    other = workspace.save_dataset("新数据集")
    other_batch = workspace.save_batch(other, "迁移批次")
    workspace.move_photos([photo], other, other_batch)
    workspace.trash("dataset", [dataset])
    workspace.empty_trash()
    assert workspace.count_photos() == 1
    assert workspace.path(workspace.photo(photo)["original"]).exists()
    workspace.trash("batch", [other_batch])
    workspace.empty_trash()
    assert workspace.count_photos() == 0
    assert not list((workspace.root / "images").iterdir())


def test_workspace_backup_preserves_trash(archive, tmp_path):
    workspace, _, _, _, photo = archive
    workspace.trash("photo", [photo])
    output, restored = tmp_path / "backup.zip", tmp_path / "restored"
    workspace.backup(output)
    Workspace.restore_backup(output, restored)
    other = Workspace(restored)
    assert len(other.trash_items()) == 1
    other.restore("photo", photo)
    assert other.count_photos() == 1
    assert other.path(other.photo(photo)["original"]).exists()


def test_search_literal_and_archived_dataset_write_protection(archive):
    workspace, dataset, batch, _, photo = archive
    workspace.update_photos([photo], tags="100%_safe")
    assert workspace.count_photos(search="%_") == 1
    assert workspace.count_photos(search="missing") == 0
    workspace.archive_dataset(dataset, True)
    assert len(workspace.datasets(False)) == 0
    with pytest.raises(ValueError):
        workspace.add_photo(Image.new("RGB", (10, 10)), dataset, batch)


def test_startup_recovers_orphaned_staging_and_uncommitted_photo(archive):
    workspace, *_ = archive
    (workspace.root / ".staging" / "interrupted").mkdir()
    (workspace.root / "images" / "orphan").mkdir()
    Workspace(workspace.root)
    assert not (workspace.root / "images" / "orphan").exists()
    assert not list((workspace.root / ".staging").iterdir())
    assert workspace.count_photos() == 1


def test_filtered_export_contains_only_matching_photos(archive, tmp_path):
    workspace, dataset, batch, _, _ = archive
    workspace.add_photo(Image.new("RGB", (10, 10)), dataset, batch, sample_id="OTHER")
    output = tmp_path / "filtered.zip"
    assert workspace.export_zip(output, dataset, filters={"search": "TOM-001"}) == 1
    restored = workspace.import_zip(output)
    assert workspace.count_photos(dataset_id=restored) == 1


def test_edit_custom_fields_preserves_capture_provenance(archive):
    workspace, _, _, _, photo = archive
    before = json.loads(workspace.photo(photo)["metadata"])
    workspace.update_photos([photo], custom={"温度": "26°C", "光照": "自然光"}, note="修正记录")
    after = json.loads(workspace.photo(photo)["metadata"])
    assert after["custom"] == {"温度": "26°C", "光照": "自然光"}
    assert after["camera"] == before["camera"]
    assert after["source_name"] == before["source_name"]
    assert workspace.photo(photo)["note"] == "修正记录"
