"""Build on each target OS; PyInstaller is intentionally not a cross compiler."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from workbench import __version__ as fallback_version  # noqa: E402
from workbench.updater import parse_version  # noqa: E402


def _resolve_build_version(cli_version: str | None) -> str:
    candidate = (cli_version or os.environ.get("VANNUS_VERSION") or "").strip()
    if not candidate:
        probe = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            candidate = probe.stdout.strip()
    if not candidate:
        candidate = fallback_version
    return parse_version(candidate).display


parser = argparse.ArgumentParser(description="Build Vannus desktop bundle with PyInstaller.")
parser.add_argument("--version", help="Explicit release version (e.g. 0.1.0 or v0.1.1)")
args = parser.parse_args()

build_dir = root / "build"
build_dir.mkdir(exist_ok=True)
build_version = _resolve_build_version(args.version)
(build_dir / "BUILD_VERSION").write_text(f"{build_version}\n", encoding="utf-8")

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

env = os.environ.copy()
env["VANNUS_BUILD_VERSION"] = build_version
subprocess.run(
    [sys.executable, "-m", "PyInstaller", "--noconfirm", str(root / "packaging/Vannus.spec")],
    cwd=root,
    env=env,
    check=True,
)
