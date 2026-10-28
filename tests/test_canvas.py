"""Native Qt widget checks; no virtual camera or simulator backend."""

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage

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
