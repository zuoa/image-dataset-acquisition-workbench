"""Build on each target OS; PyInstaller is intentionally not a cross compiler."""

import subprocess
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

root = Path(__file__).resolve().parents[1]
build_dir = root / "build"
build_dir.mkdir(exist_ok=True)

# Render the repo-native SVG into platform icons, keeping one source of truth.
# SVG-to-image rendering does not need a desktop session on CI runners.
application = QCoreApplication([])
image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
QSvgRenderer(str(root / "workbench/assets/icon.svg")).render(painter)
painter.end()
image.save(str(build_dir / "Vannus.png"))
with Image.open(build_dir / "Vannus.png") as icon:
    if sys.platform == "darwin":
        icon.save(build_dir / "Vannus.icns")
    else:
        icon.save(build_dir / "Vannus.ico", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])

subprocess.run(
    [sys.executable, "-m", "PyInstaller", "--noconfirm", str(root / "packaging/Vannus.spec")],
    cwd=root,
    check=True,
)
