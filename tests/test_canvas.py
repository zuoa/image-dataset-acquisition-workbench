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
