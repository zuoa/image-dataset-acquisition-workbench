"""Native Qt widget checks; no virtual camera or simulator backend."""

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from workbench.ui.canvas import ImageCanvas


def test_canvas_coordinates_are_in_original_image_pixels(qt_app):
    app = qt_app
    canvas = ImageCanvas(editable=True)
    canvas.resize(600, 400)
    canvas.set_image(QImage(1200, 800, QImage.Format.Format_RGB32))
    point = canvas.to_image(QPointF(150, 100))
    assert point == QPointF(300, 200)
    canvas.zoom = 2
    point = canvas.to_image(QPointF(0, 0))
    assert point == QPointF(300, 200)
    canvas.close()
    app.processEvents()


def test_resolution_change_invalidates_existing_regions(qt_app):
    app = qt_app
    canvas = ImageCanvas(editable=True)
    canvas.set_image(QImage(120, 80, QImage.Format.Format_RGB32))
    canvas.set_regions([dict(name="中心", x=10, y=10, w=20, h=20)])
    canvas.set_image(QImage(240, 160, QImage.Format.Format_RGB32))
    assert canvas.regions == []
    canvas.close()
    app.processEvents()


def test_small_preview_draws_and_moves_regions_in_source_pixels(qt_app):
    canvas = ImageCanvas(editable=True)
    canvas.resize(600, 400)
    canvas.set_image(QImage(600, 400, QImage.Format.Format_RGB32), QSize(2400, 1600))
    canvas.show()
    qt_app.processEvents()
    assert canvas.to_image(QPointF(150, 100)) == QPointF(600, 400)
    canvas.draw_mode = True
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 80))
    QTest.mouseMove(canvas, QPoint(200, 130))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(200, 130))
    region = canvas.regions[0]
    assert {key: region[key] for key in ("x", "y", "w", "h")} == dict(x=400, y=320, w=400, h=200)
    assert canvas.screen_rect(region) == QRectF(100, 80, 100, 50)
    canvas.draw_mode = False
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(150, 100))
    QTest.mouseMove(canvas, QPoint(599, 399))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(599, 399))
    assert canvas.regions[0]["x"] == 2000
    assert canvas.regions[0]["y"] == 1400
    canvas.close()
    qt_app.processEvents()


def test_preview_resize_keeps_regions_but_source_resize_invalidates_them(qt_app):
    canvas = ImageCanvas(editable=True)
    dimensions = []
    canvas.dimensions_changed.connect(lambda width, height: dimensions.append((width, height)))
    canvas.set_image(QImage(600, 400, QImage.Format.Format_RGB32), QSize(2400, 1600))
    regions = [dict(name="中心", x=1000, y=800, w=400, h=200)]
    canvas.set_regions(regions)
    canvas.set_image(QImage(900, 600, QImage.Format.Format_RGB32), QSize(2400, 1600))
    assert canvas.regions == regions
    assert dimensions == [(2400, 1600)]
    canvas.set_image(QImage(900, 600, QImage.Format.Format_RGB32), QSize(1800, 1200))
    assert not canvas.regions
    assert dimensions[-1] == (1800, 1200)
    canvas.close()


def test_canvas_four_corner_resize_zoom_pan_and_selection_signal(qt_app):
    canvas = ImageCanvas(editable=True)
    canvas.resize(600, 400)
    canvas.set_image(QImage(600, 400, QImage.Format.Format_RGB32), QSize(1200, 800))
    canvas.show()
    qt_app.processEvents()
    selections = []
    canvas.selection_changed.connect(selections.append)
    canvas.set_regions([dict(name="热区 1", x=200, y=200, w=400, h=200)])
    # Top-left handle in screen coords: (100, 100); drag to (50, 50) -> source (100, 100), size (500, 300).
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    QTest.mouseMove(canvas, QPoint(50, 50))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
    assert selections[-1] == 0
    assert {k: canvas.regions[0][k] for k in ("x", "y", "w", "h")} == dict(x=100, y=100, w=500, h=300)

    # Zoomed empty-area pan & zoom badge reset.
    canvas.zoom = 2.0
    canvas._update_overlays()
    qt_app.processEvents()
    assert canvas.zoom_badge.isVisible()
    assert "200%" in canvas.zoom_badge.text()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(500, 350))
    QTest.mouseMove(canvas, QPoint(530, 370))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(530, 370))
    assert canvas.pan == QPointF(30, 20)
    canvas.zoom_badge.click()
    assert canvas.zoom == 1.0
    assert canvas.pan == QPointF()
    assert not canvas.zoom_badge.isVisible()
    canvas.close()
    qt_app.processEvents()


def test_canvas_overlays_flash_countdown_and_interval_hud(qt_app):
    canvas = ImageCanvas(editable=True)
    canvas.resize(600, 400)
    canvas.set_image(QImage(600, 400, QImage.Format.Format_RGB32))
    canvas.show()
    qt_app.processEvents()
    stopped = []
    canvas.stop_interval_requested.connect(lambda: stopped.append(True))
    canvas.trigger_flash()
    assert canvas._flash_alpha > 0
    canvas.set_countdown(3)
    assert canvas._countdown == 3
    canvas.set_interval_status(True, remaining_s=4, shot_count=2)
    qt_app.processEvents()
    assert canvas.interval_hud.isVisible()
    assert "已拍 2 张" in canvas._interval_label.text()
    canvas.stop_interval_button.click()
    assert stopped == [True]
    canvas.set_interval_status(False)
    assert not canvas.interval_hud.isVisible()
    canvas.close()
    qt_app.processEvents()

