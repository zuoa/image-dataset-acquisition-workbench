"""Photo defaults and thumbnail caching on the native Qt backend."""

import json
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QWidget

from workbench.storage import Workspace
from workbench.ui.library import PhotoDialog
from workbench.ui.models import MediaModel, PhotoModel


@pytest.fixture
def preview_archive(tmp_path):
    workspace = Workspace(tmp_path / "preview")
    dataset = workspace.save_dataset("裁剪预览")
    batch = workspace.save_batch(dataset, "第一批")
    template = workspace.save_template(
        dataset, "中心", 120, 80, [dict(name="中心热区", x=10, y=12, w=30, h=20)]
    )
    photo_ids = []
    for color in ("red", "green"):
        image = Image.new("RGB", (120, 80), "blue")
        image.paste(color, (10, 12, 40, 32))
        image.paste("yellow", (50, 40, 60, 50))
        photo_ids.append(workspace.add_photo(image, dataset, batch, regions=template["regions"]))
    return workspace, dataset, photo_ids


def thumbnails(model, qt_app):
    for index in range(model.rowCount()):
        model.index(index).data(Qt.ItemDataRole.DecorationRole)
    assert model.pool.waitForDone(5000)
    qt_app.processEvents()
    return {
        row["id"]: model.index(index).data(Qt.ItemDataRole.DecorationRole).toImage().pixelColor(220, 150)
        for index, row in enumerate(model.rows)
    }


@pytest.mark.parametrize("model_type", [PhotoModel, MediaModel])
def test_thumbnails_show_crops_and_refresh_after_reprocessing(qt_app, preview_archive, model_type):
    workspace, dataset, (red_id, green_id) = preview_archive
    model = model_type(workspace)
    model.reload(dataset_id=dataset)
    colors = thumbnails(model, qt_app)
    assert colors[red_id].red() > 240 and colors[red_id].blue() < 10
    assert colors[green_id].green() > 115 and colors[green_id].blue() < 10
    for index, row in enumerate(model.rows):
        assert model.index(index).data() == Path(row["original"]).stem
    latest_template = workspace.save_template(
        dataset, "新热区", 120, 80, [dict(name="黄色区域", x=50, y=40, w=10, h=10)]
    )
    workspace.reprocess(red_id, latest_template)
    model.reload(dataset_id=dataset)
    colors = thumbnails(model, qt_app)
    assert colors[red_id].red() > 240 and colors[red_id].green() > 240
    assert colors[green_id].red() < 10
    assert len(list((workspace.root / ".thumbnails").glob("*.jpg"))) == 3


def test_detail_defaults_to_latest_crop_and_keeps_other_versions(qt_app, preview_archive):
    workspace, dataset, (photo_id, _) = preview_archive
    template = workspace.save_template(
        dataset,
        "两个热区",
        120,
        80,
        [dict(name="黄色区域", x=50, y=40, w=10, h=10), dict(name="红色区域", x=10, y=12, w=30, h=20)],
    )
    workspace.reprocess(photo_id, template)
    photo = workspace.photo(photo_id)
    latest = json.loads(photo["processing"])[-1]["crops"][0]["path"]
    # This is also the dataset's newest photo, so its cover uses the same crop.
    workspace.trash("photo", [preview_archive[2][1]])
    assert workspace.datasets()[0]["cover"] == latest
    host = QWidget()
    host.workspace = workspace
    dialog = PhotoDialog(host, photo)
    try:
        dialog.show()
        qt_app.processEvents()
        assert dialog.title.toolTip() == Path(photo["original"]).stem
        assert dialog.choice.currentIndex() == 1
        assert dialog.paths[1] == latest
        assert dialog.canvas.image.size() == QSize(10, 10)
        assert dialog.canvas.image.pixelColor(0, 0).name() == "#ffff00"
        assert dialog.choice.count() == 4  # Original, two latest crops, one older crop.
        dialog.choice.setCurrentIndex(2)
        assert dialog.canvas.image.size() == QSize(30, 20)
        assert dialog.canvas.image.pixelColor(0, 0).name() == "#ff0000"
        dialog.choice.setCurrentIndex(0)
        assert dialog.canvas.image.size() == QSize(120, 80)
        assert dialog.canvas.image.pixelColor(0, 0).name() == "#0000ff"
        dialog.choice.setCurrentIndex(3)
        assert dialog.canvas.image.size() == QSize(30, 20)
    finally:
        dialog.close()
        host.close()
        qt_app.processEvents()


def test_photo_without_regions_defaults_to_original(qt_app, preview_archive):
    workspace, dataset, _ = preview_archive
    batch = workspace.batches(dataset)[0]["id"]
    photo_id = workspace.add_photo(Image.new("RGB", (120, 80), "blue"), dataset, batch)
    photo = workspace.photo(photo_id)
    assert workspace.datasets()[0]["cover"] == photo["original"]
    host = QWidget()
    host.workspace = workspace
    dialog = PhotoDialog(host, photo)
    try:
        dialog.show()
        qt_app.processEvents()
        assert dialog.choice.currentIndex() == 0
        assert dialog.choice.count() == 1
        assert dialog.canvas.image.size() == QSize(120, 80)
        assert dialog.canvas.image.pixelColor(0, 0).name() == "#0000ff"
    finally:
        dialog.close()
        host.close()
        qt_app.processEvents()
