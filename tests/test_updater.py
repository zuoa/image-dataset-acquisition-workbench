"""Unit, local-HTTP integration, and Qt UI tests for GitHub Releases auto-update."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PySide6.QtCore import QLockFile, QSettings

from workbench.app import MainWindow
from workbench.storage import Workspace
from workbench.ui.update_dialog import UpdateDialog
from workbench.update_service import UpdateService
from workbench.updater import (
    CHECKSUMS_ASSET_NAME,
    InstallTarget,
    ReleaseAsset,
    ReleaseInfo,
    StagedUpdate,
    UpdateError,
    apply_mirror_url,
    cleanup_stale_updates,
    detect_install_target,
    evaluate_update,
    expected_sha256_for_asset,
    extract_update_archive,
    format_bytes,
    parse_github_release,
    parse_sha256sums,
    parse_version,
    platform_asset_name,
    validate_zip_members,
    verify_file_sha256,
    write_updater_script,
)


def pump(qt_app, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not predicate():
        qt_app.processEvents()
        time.sleep(0.01)
    assert predicate()


def _make_windows_zip(path: Path, payload_bytes: bytes = b"fake-exe-v2") -> str:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Vannus/Vannus.exe", payload_bytes)
        zf.writestr("Vannus/_internal/BUILD_VERSION", "0.2.0\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_macos_zip(tmp_path: Path, zip_path: Path) -> str:
    app_dir = tmp_path / "src_bundle" / "Vannus.app"
    exe = app_dir / "Contents" / "MacOS" / "Vannus"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    (app_dir / "Contents" / "Info.plist").write_text("<plist></plist>", encoding="utf-8")
    if sys.platform == "darwin":
        subprocess.run(
            ["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_dir), str(zip_path)],
            check=True,
        )
    else:
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Vannus.app/Contents/MacOS/Vannus", "#!/bin/sh\nexit 0\n")
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


class _LocalReleaseServer:
    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]], *, slow_chunk_delay: float = 0.0):
        self.routes = routes
        self.slow_chunk_delay = slow_chunk_delay
        self.requests: list[tuple[str, dict[str, str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.requests.append((self.path, headers))
                entry = outer.routes.get(self.path)
                if entry is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                status, resp_headers, body = entry
                if (
                    status == 200
                    and "etag" in {k.lower() for k in resp_headers}
                    and headers.get("if-none-match")
                ):
                    etag_val = next(v for k, v in resp_headers.items() if k.lower() == "etag")
                    if headers["if-none-match"] == etag_val:
                        self.send_response(304)
                        self.send_header("ETag", etag_val)
                        self.end_headers()
                        return
                self.send_response(status)
                for key, value in resp_headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if outer.slow_chunk_delay > 0 and len(body) > 1024:
                    step = max(1024, len(body) // 16)
                    for idx in range(0, len(body), step):
                        try:
                            self.wfile.write(body[idx : idx + step])
                            self.wfile.flush()
                        except OSError:
                            return
                        time.sleep(outer.slow_chunk_delay)
                else:
                    self.wfile.write(body)

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


# --- Pure unit tests ---


def test_version_parsing_and_semver_ordering():
    assert parse_version("0.0.4") < parse_version("0.1.0")
    assert parse_version("v0.1.0") == parse_version("0.1.0")
    assert parse_version("V0.1.0+build.123") == parse_version("0.1.0")
    assert parse_version("0.1.0-beta.1") < parse_version("0.1.0")
    assert parse_version("0.1.0-alpha.2") < parse_version("0.1.0-alpha.10")
    assert parse_version("0.1.0-1") < parse_version("0.1.0-alpha")
    assert parse_version("v0.2.3-rc.1").numeric == "0.2.3"
    assert parse_version("v0.2.3-rc.1").display == "0.2.3-rc.1"

    for invalid in ("", "latest", "0.1", "v01.0.0", "1.2.3-01", "1.2.3-"):
        with pytest.raises(UpdateError):
            parse_version(invalid)


def test_platform_asset_and_mirror_helpers():
    assert platform_asset_name("darwin", "arm64") == "Vannus-macos-arm64.zip"
    assert platform_asset_name("darwin", "x86_64") == "Vannus-macos-x64.zip"
    assert platform_asset_name("win32", "AMD64") == "Vannus-windows-x64.zip"
    assert platform_asset_name("linux", "x86_64") is None

    gh_url = "https://github.com/zuoa/image-dataset-acquisition-workbench/releases/download/0.2.0/Vannus-macos-arm64.zip"
    assert apply_mirror_url(gh_url, "") == gh_url
    assert apply_mirror_url(gh_url, "http://insecure.example") == gh_url
    assert apply_mirror_url(gh_url, "https://mirror.example") == f"https://mirror.example/{gh_url}"
    assert apply_mirror_url("https://other.example/file.zip", "https://mirror.example") == "https://other.example/file.zip"
    assert format_bytes(500) == "500 B"
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(5 * 1024 * 1024) == "5.0 MB"


def test_parse_github_release_and_sha256sums(tmp_path):
    payload = {
        "tag_name": "v0.2.0",
        "name": "Vannus 0.2.0",
        "body": "### 更新内容\n- 支持自动升级",
        "html_url": "https://github.com/zuoa/image-dataset-acquisition-workbench/releases/tag/v0.2.0",
        "published_at": "2026-11-28T04:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "Vannus-macos-arm64.zip",
                "browser_download_url": "https://github.com/zuoa/repo/releases/download/v0.2.0/Vannus-macos-arm64.zip",
                "size": 123456,
                "content_type": "application/zip",
            },
            {
                "name": CHECKSUMS_ASSET_NAME,
                "browser_download_url": "https://github.com/zuoa/repo/releases/download/v0.2.0/SHA256SUMS.txt",
                "size": 256,
                "content_type": "text/plain",
            },
        ],
    }
    release = parse_github_release(payload, platform_name="darwin", machine="arm64")
    assert release.version == parse_version("0.2.0")
    assert release.asset is not None and release.asset.name == "Vannus-macos-arm64.zip"
    assert release.checksums_asset is not None and release.checksums_asset.name == CHECKSUMS_ASSET_NAME

    target = InstallTarget("macos_app", True, tmp_path / "Vannus.app", tmp_path / "Vannus.app/Contents/MacOS/Vannus")
    result = evaluate_update("0.1.0", release, target)
    assert result.update_available
    assert result.can_auto_install

    # Missing SHA256SUMS.txt degrades gracefully to fallback
    payload_no_sum = {**payload, "assets": [payload["assets"][0]]}
    release_no_sum = parse_github_release(payload_no_sum, platform_name="darwin", machine="arm64")
    result_no_sum = evaluate_update("0.1.0", release_no_sum, target)
    assert result_no_sum.update_available
    assert not result_no_sum.can_auto_install
    assert CHECKSUMS_ASSET_NAME in result_no_sum.fallback_reason

    with pytest.raises(UpdateError):
        parse_github_release({**payload, "draft": True})
    with pytest.raises(UpdateError):
        parse_github_release({**payload, "prerelease": True})

    digest = hashlib.sha256(b"hello-vannus").hexdigest()
    checksums_text = f"{digest}  ./Vannus-macos-arm64.zip\r\n{'a' * 64} *Vannus-windows-x64.zip\r\n"
    parsed = parse_sha256sums(checksums_text)
    assert parsed["Vannus-macos-arm64.zip"] == digest
    assert parsed["Vannus-windows-x64.zip"] == "a" * 64
    assert expected_sha256_for_asset(checksums_text, "Vannus-macos-arm64.zip") == digest
    with pytest.raises(UpdateError):
        expected_sha256_for_asset(checksums_text, "Vannus-macos-x64.zip")

    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"hello-vannus")
    assert verify_file_sha256(sample, digest) == digest
    with pytest.raises(UpdateError):
        verify_file_sha256(sample, "0" * 64)


def test_zip_slip_and_symlink_protection(tmp_path):
    bad_rel = tmp_path / "traversal.zip"
    with zipfile.ZipFile(bad_rel, "w") as zf:
        zf.writestr("../escape.txt", "boom")
    with pytest.raises(UpdateError):
        validate_zip_members(bad_rel)

    bad_abs = tmp_path / "abs.zip"
    with zipfile.ZipFile(bad_abs, "w") as zf:
        zf.writestr("/etc/passwd", "boom")
    with pytest.raises(UpdateError):
        validate_zip_members(bad_abs)

    bad_drive = tmp_path / "drive.zip"
    with zipfile.ZipFile(bad_drive, "w") as zf:
        zf.writestr("C:\\Windows\\evil.dll", "boom")
    with pytest.raises(UpdateError):
        validate_zip_members(bad_drive)

    bad_symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(bad_symlink, "w") as zf:
        info = zipfile.ZipInfo("Vannus/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(UpdateError):
        validate_zip_members(bad_symlink, reject_symlinks=True)

    valid_win_zip = tmp_path / "valid_win.zip"
    _make_windows_zip(valid_win_zip, payload_bytes=b"win-binary")
    extracted = extract_update_archive(valid_win_zip, tmp_path / "win_out", "windows_dir")
    assert (extracted / "Vannus.exe").read_bytes() == b"win-binary"


def test_detect_install_target_modes(tmp_path):
    source_target = detect_install_target(Path("/usr/bin/python3"), frozen=False, platform_name="darwin")
    assert source_target.kind == "source"
    assert not source_target.can_auto_update

    # Writable macOS .app
    app_dir = tmp_path / "Apps" / "Vannus.app"
    mac_exe = app_dir / "Contents" / "MacOS" / "Vannus"
    mac_exe.parent.mkdir(parents=True)
    mac_exe.write_text("", encoding="utf-8")
    mac_target = detect_install_target(mac_exe, frozen=True, platform_name="darwin")
    assert mac_target.kind == "macos_app"
    assert mac_target.can_auto_update
    assert mac_target.install_path == app_dir

    # Translocated macOS path
    translocated_exe = Path(
        "/private/var/folders/xx/yy/T/AppTranslocation/12345/d/Vannus.app/Contents/MacOS/Vannus"
    )
    translocated_target = detect_install_target(translocated_exe, frozen=True, platform_name="darwin")
    assert translocated_target.kind == "macos_app"
    assert not translocated_target.can_auto_update
    assert "应用程序" in translocated_target.reason

    # Writable Windows onedir
    win_dir = tmp_path / "WinApps" / "Vannus"
    win_exe = win_dir / "Vannus.exe"
    win_dir.mkdir(parents=True)
    win_exe.write_bytes(b"exe")
    win_target = detect_install_target(win_exe, frozen=True, platform_name="win32")
    assert win_target.kind == "windows_dir"
    assert win_target.can_auto_update
    assert win_target.install_path == win_dir


def test_updater_scripts_and_cleanup(tmp_path):
    sh_script = write_updater_script(tmp_path, "macos_app")
    assert sh_script.name == "apply_update.sh"
    if sys.platform != "win32":
        check = subprocess.run(["/bin/sh", "-n", str(sh_script)], capture_output=True, text=True, check=False)
        assert check.returncode == 0, check.stderr

    ps_script = write_updater_script(tmp_path, "windows_dir")
    assert ps_script.name == "apply_update.ps1"
    ps_content = ps_script.read_text(encoding="ascii")
    assert "param(" in ps_content
    assert "Restore-Backup" in ps_content

    # Exercise macOS rollback path safely (staged app lacks Contents/MacOS/Vannus so `open` is never called)
    if sys.platform == "darwin":
        target_app = tmp_path / "Vannus.app"
        marker = target_app / "Contents" / "MacOS" / "Vannus"
        marker.parent.mkdir(parents=True)
        marker.write_text("original-v1", encoding="utf-8")
        marker.chmod(0o755)
        bad_staged = tmp_path / "stage-bad" / "extracted" / "Vannus.app"
        bad_staged.mkdir(parents=True)
        backup_app = tmp_path / "Vannus.app.bak-test"
        log_file = tmp_path / "logs" / "updater.log"
        # Pass a finished child PID so wait loop exits immediately
        done_proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        done_proc.wait()
        run = subprocess.run(
            ["/bin/sh", str(sh_script), str(done_proc.pid), str(target_app), str(bad_staged), str(backup_app), str(log_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode != 0
        assert marker.read_text(encoding="utf-8") == "original-v1"
        assert "Restoring previous app from backup" in log_file.read_text(encoding="utf-8")

    # Stale stage and backup cleanup
    updates_dir = tmp_path / "updates"
    stale_stage = updates_dir / "stage-0.2.0-abcd"
    stale_stage.mkdir(parents=True)
    (stale_stage / "temp.zip").write_bytes(b"123")
    (updates_dir / "apply_update.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    stale_bak = tmp_path / "Vannus.app.bak-9999"
    stale_bak.mkdir(exist_ok=True)
    target = InstallTarget("macos_app", True, tmp_path / "Vannus.app", tmp_path / "Vannus.app/Contents/MacOS/Vannus")
    cleanup_stale_updates(updates_dir, target)
    assert not stale_stage.exists()
    assert not (updates_dir / "apply_update.sh").exists()
    assert not stale_bak.exists()


# --- Local HTTP server integration tests ---


def test_update_service_etag_cache_and_download_verify_extract(qt_app, tmp_path):
    zip_path = tmp_path / "fixture.zip"
    if sys.platform == "darwin":
        asset_name = "Vannus-macos-arm64.zip"
        expected_sha = _make_macos_zip(tmp_path, zip_path)
        target_kind = "macos_app"
        plat, mach = "darwin", "arm64"
    else:
        asset_name = "Vannus-windows-x64.zip"
        expected_sha = _make_windows_zip(zip_path)
        target_kind = "windows_dir"
        plat, mach = "win32", "AMD64"

    zip_bytes = zip_path.read_bytes()
    checksums_bytes = f"{expected_sha}  {asset_name}\n".encode("utf-8")

    routes = {
        "/releases/latest": (
            200,
            {"Content-Type": "application/json", "ETag": '"etag-v020"'},
            b"",
        ),
        f"/download/{asset_name}": (200, {"Content-Type": "application/zip"}, zip_bytes),
        f"/download/{CHECKSUMS_ASSET_NAME}": (200, {"Content-Type": "text/plain"}, checksums_bytes),
    }

    with _LocalReleaseServer(routes) as server:
        release_json = json.dumps(
            {
                "tag_name": "v0.2.0",
                "name": "Vannus 0.2.0",
                "body": "修复若干问题并支持自动升级",
                "html_url": f"{server.base_url}/releases/tag/v0.2.0",
                "published_at": "2026-11-28T05:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": asset_name,
                        "browser_download_url": f"{server.base_url}/download/{asset_name}",
                        "size": len(zip_bytes),
                    },
                    {
                        "name": CHECKSUMS_ASSET_NAME,
                        "browser_download_url": f"{server.base_url}/download/{CHECKSUMS_ASSET_NAME}",
                        "size": len(checksums_bytes),
                    },
                ],
            }
        ).encode("utf-8")
        server.routes["/releases/latest"] = (
            200,
            {"Content-Type": "application/json", "ETag": '"etag-v020"'},
            release_json,
        )

        prefs = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
        install_dir = tmp_path / ("Vannus.app" if target_kind == "macos_app" else "Vannus")
        install_dir.mkdir()
        target = InstallTarget(target_kind, True, install_dir, install_dir / "Vannus")
        service = UpdateService(
            prefs,
            tmp_path / "updates",
            current_version="0.1.0",
            api_url=f"{server.base_url}/releases/latest",
            install_target=target,
            platform_name=plat,
            machine=mach,
            use_system_proxy=False,
            verify_signature=False,
        )

        results = []
        service.check_finished.connect(lambda res, manual: results.append((res, manual)))

        # First check: 200 OK and persists ETag cache
        assert service.check_for_updates(manual=False)
        pump(qt_app, lambda: len(results) == 1)
        first_result, first_manual = results[0]
        assert not first_manual
        assert first_result.update_available
        assert first_result.can_auto_install

        # Second check: sends If-None-Match and resolves via 304 Not Modified
        assert service.check_for_updates(manual=True)
        pump(qt_app, lambda: len(results) == 2)
        assert server.requests[1][1].get("if-none-match") == '"etag-v020"'
        assert results[1][0].latest_release.version == parse_version("0.2.0")

        # Download + verify + extract
        staged_items: list[StagedUpdate] = []
        errors: list[str] = []
        progress_events: list[tuple[int, int]] = []
        service.update_ready.connect(staged_items.append)
        service.update_failed.connect(errors.append)
        service.download_progress.connect(lambda r, t: progress_events.append((r, t)))

        assert service.start_download(first_result.latest_release)
        pump(qt_app, lambda: bool(staged_items) or bool(errors), seconds=10)
        assert not errors
        assert len(staged_items) == 1
        staged = staged_items[0]
        assert staged.sha256 == expected_sha
        assert staged.payload_path.is_dir()
        assert progress_events
        service.shutdown()
        service.deleteLater()
        qt_app.processEvents()


def test_update_service_sha256_mismatch_and_cancel_and_rate_limit(qt_app, tmp_path):
    zip_path = tmp_path / "win.zip"
    _make_windows_zip(zip_path, payload_bytes=b"X" * 200_000)
    zip_bytes = zip_path.read_bytes()
    wrong_checksums = f"{'0' * 64}  Vannus-windows-x64.zip\n".encode("utf-8")

    routes = {
        "/rate-limited": (403, {"X-RateLimit-Remaining": "0"}, b'{"message":"API rate limit exceeded"}'),
        "/download/Vannus-windows-x64.zip": (200, {"Content-Type": "application/zip"}, zip_bytes),
        f"/download/{CHECKSUMS_ASSET_NAME}": (200, {"Content-Type": "text/plain"}, wrong_checksums),
    }

    with _LocalReleaseServer(routes, slow_chunk_delay=0.05) as server:
        prefs = QSettings(str(tmp_path / "prefs2.ini"), QSettings.Format.IniFormat)
        install_dir = tmp_path / "Vannus"
        install_dir.mkdir()
        target = InstallTarget("windows_dir", True, install_dir, install_dir / "Vannus.exe")
        service = UpdateService(
            prefs,
            tmp_path / "updates2",
            current_version="0.1.0",
            api_url=f"{server.base_url}/rate-limited",
            install_target=target,
            platform_name="win32",
            machine="AMD64",
            use_system_proxy=False,
        )

        # Rate limit check failure
        check_errors = []
        service.check_failed.connect(lambda msg, manual: check_errors.append((msg, manual)))
        service.check_for_updates(manual=True)
        pump(qt_app, lambda: len(check_errors) == 1)
        assert "频率上限" in check_errors[0][0]

        release = ReleaseInfo(
            tag_name="v0.2.0",
            version=parse_version("0.2.0"),
            name="Vannus 0.2.0",
            body="",
            html_url=f"{server.base_url}/releases/tag/v0.2.0",
            published_at="",
            asset=ReleaseAsset("Vannus-windows-x64.zip", f"{server.base_url}/download/Vannus-windows-x64.zip", len(zip_bytes)),
            checksums_asset=ReleaseAsset(CHECKSUMS_ASSET_NAME, f"{server.base_url}/download/{CHECKSUMS_ASSET_NAME}", len(wrong_checksums)),
        )

        # Cancel mid-download cleans up stage dir
        cancelled = []
        service.update_cancelled.connect(lambda: cancelled.append(True))
        service.download_progress.connect(lambda r, _t: service.cancel_download() if r > 0 else None)
        assert service.start_download(release)
        pump(qt_app, lambda: bool(cancelled), seconds=10)
        assert not list((tmp_path / "updates2").glob("stage-*"))

        # Without cancel: SHA256 mismatch fails and cleans up stage dir
        service.download_progress.disconnect()
        server.slow_chunk_delay = 0.0
        failures = []
        service.update_failed.connect(failures.append)
        assert service.start_download(release)
        pump(qt_app, lambda: bool(failures), seconds=10)
        assert "SHA256" in failures[0]
        assert not list((tmp_path / "updates2").glob("stage-*"))
        service.shutdown()
        service.deleteLater()
        qt_app.processEvents()


# --- UI integration tests (zero network calls) ---


def test_mainwindow_settings_and_update_dialog_ui(qt_app, tmp_path):
    from qfluentwidgets import Theme, setTheme

    setTheme(Theme.LIGHT)
    workspace = Workspace(tmp_path / "workspace")
    lock = QLockFile(str(workspace.root / ".fieldwork.lock"))
    assert lock.tryLock(100)
    prefs = QSettings(str(tmp_path / "ui_prefs.ini"), QSettings.Format.IniFormat)
    window = MainWindow(workspace, prefs, tmp_path / "logs", lock, update_dir=tmp_path / "updates")
    try:
        # Constructing MainWindow must never start a background update check automatically.
        assert not window.updater.checking
        assert window.updater.auto_check is True

        # Settings toggle persists to QSettings
        window.settings.auto_check_updates.setChecked(False)
        qt_app.processEvents()
        assert window.updater.auto_check is False
        assert prefs.value("updates/auto_check", True, type=bool) is False

        # Simulate a silent update check finding a newer release in source mode
        release = ReleaseInfo(
            tag_name="v0.2.0",
            version=parse_version("0.2.0"),
            name="Vannus 0.2.0",
            body="- 新增自动升级支持",
            html_url="https://github.com/zuoa/image-dataset-acquisition-workbench/releases/tag/v0.2.0",
            published_at="2026-11-28T05:00:00Z",
            asset=ReleaseAsset("Vannus-macos-arm64.zip", "https://example.com/a.zip", 10_000_000),
            checksums_asset=ReleaseAsset(CHECKSUMS_ASSET_NAME, "https://example.com/sum.txt", 128),
        )
        source_result = evaluate_update(
            "0.1.0",
            release,
            InstallTarget("source", False, None, Path(sys.executable), "当前从源码环境运行，请通过发布页或 Git 拉取代码更新。"),
        )
        window.updater.last_result = source_result
        window.updater.state_changed.emit()
        window._on_update_check_finished(source_result, False)
        qt_app.processEvents()

        assert window.settings_nav_item._badge is True
        assert not window.settings.install_update_button.isHidden()

        # UpdateDialog renders fallback banner in source mode and supports ignoring version
        dialog = UpdateDialog(window, source_result, parent=window)
        assert not dialog.notice_card.isHidden()
        assert dialog.yesButton.text() == "打开发布页"
        dialog._on_ignore_clicked()
        qt_app.processEvents()
        assert window.updater.ignored_version == "0.2.0"
        assert window.settings_nav_item._badge is False

        # apply_staged_update is blocked when runner is busy
        dummy_staged = StagedUpdate(
            release=release,
            stage_dir=tmp_path / "stage",
            archive_path=tmp_path / "stage" / "a.zip",
            payload_path=tmp_path / "stage" / "extracted" / "Vannus.app",
            sha256="a" * 64,
        )
        window.runner.job = object()
        try:
            assert window.apply_staged_update(dummy_staged) is False
        finally:
            window.runner.job = None
    finally:
        window.close()
        qt_app.processEvents()
