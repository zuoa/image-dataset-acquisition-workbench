from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
icon = str(root / 'build' / ('Vannus.icns' if sys.platform == 'darwin' else 'Vannus.ico'))
analysis = Analysis(
    [str(root / 'packaging/entry.py')], pathex=[str(root)],
    binaries=[], datas=collect_data_files('qfluentwidgets') + [(str(root / 'workbench/assets'), 'workbench/assets'), (str(root / 'THIRD_PARTY_NOTICES.md'), '.')],
    hiddenimports=['PySide6.QtSvg', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets'],
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
                             'NSHighResolutionCapable': True, 'CFBundleShortVersionString': '0.1.0'})
