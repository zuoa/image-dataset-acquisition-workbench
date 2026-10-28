"""Product identity and a one-time migration of existing desktop preferences."""

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtGui import QIcon

NAME = "Vannus"
TITLE = f"{NAME} · 图像数据集采集工作台"
ICON_PATH = Path(__file__).parent / "assets" / "icon.svg"


def configure_application(app):
    # Ask Qt for the previous platform-specific location before changing identity.
    legacy_name = "Fieldwork"
    app.setApplicationName(legacy_name)
    app.setOrganizationName(legacy_name)
    legacy_preferences = QSettings()
    legacy_data_dir = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    )

    app.setApplicationName(NAME)
    app.setOrganizationName(NAME)
    app.setApplicationDisplayName(TITLE)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    preferences = QSettings()
    if not preferences.value("branding/migrated", False, type=bool):
        for key in legacy_preferences.allKeys():
            if not preferences.contains(key):
                preferences.setValue(key, legacy_preferences.value(key))
        previous_workspace = legacy_data_dir / "workspace"
        if not preferences.contains("workspace") and (previous_workspace / "archive.sqlite3").is_file():
            preferences.setValue("workspace", str(previous_workspace))
        preferences.setValue("branding/migrated", True)
        preferences.sync()
    return preferences
