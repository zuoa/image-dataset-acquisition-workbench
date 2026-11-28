import os
from pathlib import Path
import re
import sys
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
build_dir = root / 'build'
build_dir.mkdir(exist_ok=True)
version_file = build_dir / 'BUILD_VERSION'
build_version = (
    os.environ.get('VANNUS_BUILD_VERSION')
    or (version_file.read_text(encoding='utf-8').strip() if version_file.is_file() else '')
    or '0.1.0'
).lstrip('vV')
if not version_file.is_file():
    version_file.write_text(f'{build_version}\n', encoding='utf-8')
bundle_version_match = re.match(r'^(\d+\.\d+\.\d+)', build_version)
bundle_version = bundle_version_match.group(1) if bundle_version_match else '0.1.0'

icon = str(root / 'build' / ('Vannus.icns' if sys.platform == 'darwin' else 'Vannus.ico'))
analysis = Analysis(
    [str(root / 'packaging/entry.py')], pathex=[str(root)],
    binaries=[],
    datas=collect_data_files('qfluentwidgets') + [
        (str(root / 'workbench/assets'), 'workbench/assets'),
        (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
        (str(version_file), '.'),
    ],
    hiddenimports=['PySide6.QtSvg', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtNetwork'],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick', 'matplotlib', 'scipy', 'pytest'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='Vannus', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False, icon=icon,
          entitlements_file=str(root / 'packaging/entitlements.plist') if sys.platform == 'darwin' else None)
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='Vannus')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Vannus.app', icon=icon, bundle_identifier='app.vannus.acquisition',
                 info_plist={'CFBundleDisplayName': 'Vannus',
                             'NSCameraUsageDescription': '连接 USB 摄像头以预览、拍照及录制实验视频。',
                             'NSCameraUseContinuityCameraDeviceType': True,
                             'NSHighResolutionCapable': True,
                             'CFBundleShortVersionString': bundle_version,
                             'CFBundleVersion': bundle_version})
