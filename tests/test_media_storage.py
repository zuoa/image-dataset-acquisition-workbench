"""Mixed-media browsing and export; media bytes are opaque archive fixtures."""

import json
import zipfile

import pytest
from PIL import Image

from workbench.storage import Workspace


@pytest.fixture
def mixed_archive(tmp_path):
    workspace = Workspace(tmp_path / "mixed")
    dataset = workspace.save_dataset("混合素材")
    batch = workspace.save_batch(dataset, "第一批")
    other_batch = workspace.save_batch(dataset, "第二批")
    rows = []
    for number, (kind, target, name) in enumerate(
        (
            ("photo", batch, "shared%_photo"),
            ("video", batch, "shared%_video"),
            ("photo", other_batch, "other photo"),
            ("video", other_batch, "other video"),
        ),
        start=1,
    ):
        if kind == "photo":
            item_id = workspace.add_photo(Image.new("RGB", (12, 8), "teal"), dataset, target, sample_id=name)
        else:
            source = workspace.new_recording_path()
            source.write_bytes(b"opaque video archive fixture")
            item_id = workspace.add_video(source, dataset, target, duration_ms=3000, sample_id=name)
        with workspace.connect() as db:
            table = "photos" if kind == "photo" else "videos"
            db.execute(
                f"UPDATE {table} SET created_at=? WHERE id=?", (f"2026-10-0{number}T12:00:00Z", item_id)
            )
        rows.append((kind, item_id))
    return workspace, dataset, batch, other_batch, rows


def test_mixed_browsing_order_pagination_shared_filters_and_parent_visibility(mixed_archive):
    workspace, dataset, batch, other_batch, rows = mixed_archive
    expected = [item_id for _, item_id in reversed(rows)]
    assert workspace.media_counts(dataset_id=dataset) == {"photo": 2, "video": 2}
    assert [row["id"] for row in workspace.media(dataset_id=dataset)] == expected
    pages = workspace.media(dataset_id=dataset, limit=2) + workspace.media(
        dataset_id=dataset, offset=2, limit=2
    )
    assert [row["id"] for row in pages] == expected
    assert workspace.media_counts(dataset_id=dataset, media_type="photo") == {"photo": 2, "video": 0}
    assert workspace.media_counts(dataset_id=dataset, media_type="video") == {"photo": 0, "video": 2}
    assert [row["id"] for row in workspace.media(batch_id=batch, search="%_")] == expected[2:]
    assert [row["id"] for row in workspace.media(date_from="2026-10-02", date_to="2026-10-04")] == expected[
        1:3
    ]
    assert not workspace.media(dataset_id="missing")
    assert not workspace.media(search="missing")
    workspace.trash("batch", [other_batch])
    assert workspace.media_counts(dataset_id=dataset) == {"photo": 1, "video": 1}
    workspace.trash("dataset", [dataset])
    assert not workspace.media()
    workspace.restore("dataset", dataset)
    assert workspace.media_counts(dataset_id=dataset) == {"photo": 1, "video": 1}


@pytest.mark.parametrize("media_type,photos,videos", [("all", 1, 1), ("photo", 1, 0), ("video", 0, 1)])
def test_filtered_export_preserves_media_type_and_other_filters(
    mixed_archive, tmp_path, media_type, photos, videos
):
    workspace, dataset, batch, _, _ = mixed_archive
    filters = {"media_type": media_type, "batch_id": batch, "search": "%_"}
    output = tmp_path / f"{media_type}.zip"
    assert workspace.export_zip(output, dataset, filters=filters) == photos + videos
    assert filters["media_type"] == media_type
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert len(manifest["photos"]) == photos
    assert len(manifest["videos"]) == videos
    restored = workspace.import_zip(output)
    assert workspace.media_counts(dataset_id=restored) == {"photo": photos, "video": videos}
