"""Pure-Python GitHub Releases auto-update core (zero Qt dependencies)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

REPO_OWNER = "zuoa"
REPO_NAME = "image-dataset-acquisition-workbench"
LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"
CHECKSUMS_ASSET_NAME = "SHA256SUMS.txt"

_VERSION_RE = re.compile(
    r"^[vV]?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256_LINE_RE = re.compile(r"^(?P<digest>[0-9a-fA-F]{64})\s+\*?(?P<name>.+?)\s*$")
_GITHUB_HOST_PREFIX = "https://github.com/"
_SYMLINK_MODE = 0o120000
_CHUNK_SIZE = 1024 * 1024


class UpdateError(RuntimeError):
    """Raised when an update check, download, verification, or install step fails."""


class UpdateCancelled(UpdateError):
    """Raised when the user cancels an in-progress update download or extraction."""


@dataclass(frozen=True, order=False)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...] = ()
    raw: str = ""

    @property
    def numeric(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def display(self) -> str:
        if self.prerelease:
            suffix = ".".join(str(part) for part in self.prerelease)
            return f"{self.numeric}-{suffix}"
        return self.numeric

    def _core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._core() == other._core() and self.prerelease == other.prerelease

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self._core() != other._core():
            return self._core() < other._core()
        if self.prerelease == other.prerelease:
            return False
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if isinstance(left, int) and isinstance(right, int):
                return left < right
            if isinstance(left, int) != isinstance(right, int):
                return isinstance(left, int)
            return str(left) < str(right)
        return len(self.prerelease) < len(other.prerelease)

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self == other or self < other

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return other < self

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self == other or other < self


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    content_type: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: Version
    name: str
    body: str
    html_url: str
    published_at: str
    asset: ReleaseAsset | None
    checksums_asset: ReleaseAsset | None


@dataclass(frozen=True)
class InstallTarget:
    kind: Literal["macos_app", "windows_dir", "source", "unsupported"]
    can_auto_update: bool
    install_path: Path | None
    executable_path: Path | None
    reason: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: Version
    latest_release: ReleaseInfo | None
    update_available: bool
    install_target: InstallTarget
    fallback_reason: str = ""

    @property
    def can_auto_install(self) -> bool:
        return (
            self.update_available
            and self.latest_release is not None
            and self.install_target.can_auto_update
            and self.latest_release.asset is not None
            and self.latest_release.checksums_asset is not None
        )


@dataclass(frozen=True)
class StagedUpdate:
    release: ReleaseInfo
    stage_dir: Path
    archive_path: Path
    payload_path: Path
    sha256: str


def parse_version(text: str) -> Version:
    cleaned = str(text or "").strip()
    match = _VERSION_RE.match(cleaned)
    if not match:
        raise UpdateError(f"无法解析版本号：{cleaned!r}")
    prerelease_raw = match.group("prerelease")
    prerelease: list[int | str] = []
    if prerelease_raw:
        for identifier in prerelease_raw.split("."):
            if not identifier:
                raise UpdateError(f"无效的预发布版本号：{cleaned!r}")
            if identifier.isdigit():
                if len(identifier) > 1 and identifier.startswith("0"):
                    raise UpdateError(f"预发布数字段不能包含前导零：{cleaned!r}")
                prerelease.append(int(identifier))
            else:
                prerelease.append(identifier)
    return Version(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=tuple(prerelease),
        raw=cleaned,
    )


def format_bytes(size: int) -> str:
    value = max(0, int(size))
    if value < 1024:
        return f"{value} B"
    if value < 1024**2:
        return f"{value / 1024:.1f} KB"
    if value < 1024**3:
        return f"{value / 1024**2:.1f} MB"
    return f"{value / 1024**3:.2f} GB"


def platform_asset_name(
    platform_name: str | None = None,
    machine: str | None = None,
) -> str | None:
    plat = (platform_name or sys.platform).lower()
    arch = (machine or platform.machine()).lower()
    if plat == "darwin":
        if arch in ("arm64", "aarch64"):
            return "Vannus-macos-arm64.zip"
        if arch in ("x86_64", "amd64"):
            return "Vannus-macos-x64.zip"
        return None
    if plat == "win32":
        if arch in ("amd64", "x86_64"):
            return "Vannus-windows-x64.zip"
        return None
    return None


def apply_mirror_url(url: str, mirror_prefix: str = "") -> str:
    prefix = str(mirror_prefix or "").strip()
    if not prefix or not prefix.startswith("https://"):
        return url
    if not url.startswith(_GITHUB_HOST_PREFIX):
        return url
    normalized = prefix if prefix.endswith("/") else f"{prefix}/"
    return f"{normalized}{url}"


def parse_github_release(
    payload: dict,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> ReleaseInfo:
    if not isinstance(payload, dict):
        raise UpdateError("GitHub Release 响应格式无效")
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("最新发布版本为草稿或预发布版本，已跳过")
    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateError("GitHub Release 缺少 tag_name 字段")
    version = parse_version(tag_name)
    html_url = str(payload.get("html_url") or RELEASES_PAGE_URL).strip() or RELEASES_PAGE_URL
    name = str(payload.get("name") or tag_name).strip()
    body = str(payload.get("body") or "").strip()
    published_at = str(payload.get("published_at") or "").strip()

    expected_asset_name = platform_asset_name(platform_name, machine)
    matched_asset: ReleaseAsset | None = None
    checksums_asset: ReleaseAsset | None = None
    for item in payload.get("assets") or []:
        if not isinstance(item, dict):
            continue
        asset_name = str(item.get("name") or "").strip()
        download_url = str(item.get("browser_download_url") or "").strip()
        if not asset_name or not download_url:
            continue
        asset = ReleaseAsset(
            name=asset_name,
            download_url=download_url,
            size=int(item.get("size") or 0),
            content_type=str(item.get("content_type") or ""),
        )
        if expected_asset_name and asset_name == expected_asset_name:
            matched_asset = asset
        elif asset_name == CHECKSUMS_ASSET_NAME:
            checksums_asset = asset

    return ReleaseInfo(
        tag_name=tag_name,
        version=version,
        name=name,
        body=body,
        html_url=html_url,
        published_at=published_at,
        asset=matched_asset,
        checksums_asset=checksums_asset,
    )


def parse_sha256sums(text: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SHA256_LINE_RE.match(stripped)
        if not match:
            continue
        filename = match.group("name").strip()
        if filename.startswith("./"):
            filename = filename[2:]
        checksums[filename] = match.group("digest").lower()
    return checksums


def expected_sha256_for_asset(checksums_text: str, asset_name: str) -> str:
    checksums = parse_sha256sums(checksums_text)
    digest = checksums.get(asset_name)
    if not digest:
        raise UpdateError(f"校验文件 {CHECKSUMS_ASSET_NAME} 中未找到 {asset_name} 的 SHA256 记录")
    return digest


def verify_file_sha256(path: Path, expected_hex: str) -> str:
    normalized_expected = str(expected_hex or "").strip().lower()
    if len(normalized_expected) != 64:
        raise UpdateError("预期的 SHA256 校验值格式无效")
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            hasher.update(chunk)
    actual = hasher.hexdigest().lower()
    if not hmac.compare_digest(actual, normalized_expected):
        raise UpdateError("安装包 SHA256 校验失败，文件可能已损坏或被篡改")
    return actual


def is_macos_translocated(path: Path) -> bool:
    text = str(path)
    resolved = str(path.resolve()) if path.exists() else text
    for candidate in (text, resolved):
        if "/AppTranslocation/" in candidate:
            return True
        if candidate.startswith("/Volumes/"):
            return True
    return False


def is_directory_writable(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    probe = directory / f".vannus-write-test-{secrets.token_hex(6)}"
    try:
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def detect_install_target(
    executable: Path | None = None,
    *,
    frozen: bool | None = None,
    platform_name: str | None = None,
) -> InstallTarget:
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    exe = Path(executable or sys.executable)
    plat = (platform_name or sys.platform).lower()

    if not is_frozen:
        return InstallTarget(
            kind="source",
            can_auto_update=False,
            install_path=None,
            executable_path=exe,
            reason="当前从源码环境运行，请通过发布页或 Git 拉取代码更新。",
        )

    if plat == "darwin":
        app_path = _find_macos_app_bundle(exe)
        if app_path is None:
            return InstallTarget(
                kind="unsupported",
                can_auto_update=False,
                install_path=None,
                executable_path=exe,
                reason="未识别到标准 macOS .app 应用包结构，请前往发布页手动下载更新。",
            )
        if is_macos_translocated(app_path):
            return InstallTarget(
                kind="macos_app",
                can_auto_update=False,
                install_path=app_path,
                executable_path=exe,
                reason="当前应用位于只读磁盘映像或临时隔离目录，请先拖入「应用程序」文件夹，或前往发布页手动更新。",
            )
        if not (is_directory_writable(app_path.parent) and is_directory_writable(app_path)):
            return InstallTarget(
                kind="macos_app",
                can_auto_update=False,
                install_path=app_path,
                executable_path=exe,
                reason=f"当前安装目录（{app_path.parent}）缺少写入权限，请前往发布页手动下载替换。",
            )
        return InstallTarget(
            kind="macos_app",
            can_auto_update=True,
            install_path=app_path,
            executable_path=exe,
        )

    if plat == "win32":
        install_dir = exe.parent
        if not (is_directory_writable(install_dir) and is_directory_writable(install_dir.parent)):
            return InstallTarget(
                kind="windows_dir",
                can_auto_update=False,
                install_path=install_dir,
                executable_path=exe,
                reason=f"当前安装目录（{install_dir}）缺少写入权限，请前往发布页手动下载替换。",
            )
        return InstallTarget(
            kind="windows_dir",
            can_auto_update=True,
            install_path=install_dir,
            executable_path=exe,
        )

    return InstallTarget(
        kind="unsupported",
        can_auto_update=False,
        install_path=None,
        executable_path=exe,
        reason="当前操作系统暂不支持应用内自动替换，请前往发布页手动下载。",
    )


def _find_macos_app_bundle(executable: Path) -> Path | None:
    parts = executable.parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index].endswith(".app"):
            candidate = Path(*parts[: index + 1])
            if index + 2 < len(parts) and parts[index + 1] == "Contents" and parts[index + 2] == "MacOS":
                return candidate
    return None


def evaluate_update(
    current_version_str: str,
    release: ReleaseInfo | None,
    install_target: InstallTarget,
) -> UpdateCheckResult:
    current = parse_version(current_version_str)
    if release is None:
        return UpdateCheckResult(
            current_version=current,
            latest_release=None,
            update_available=False,
            install_target=install_target,
        )
    available = release.version > current
    fallback_reason = ""
    if available:
        if not install_target.can_auto_update:
            fallback_reason = install_target.reason
        elif release.asset is None:
            fallback_reason = "该版本未包含适配当前系统架构的安装包，请前往发布页查看。"
        elif release.checksums_asset is None:
            fallback_reason = f"该版本缺少 {CHECKSUMS_ASSET_NAME} 校验文件，无法进行安全自动安装，请前往发布页手动下载。"
    return UpdateCheckResult(
        current_version=current,
        latest_release=release,
        update_available=available,
        install_target=install_target,
        fallback_reason=fallback_reason,
    )


def validate_zip_members(archive_path: Path, *, reject_symlinks: bool = False) -> None:
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                name = info.filename
                if not name:
                    continue
                if name.startswith(("/", "\\")):
                    raise UpdateError(f"压缩包包含非法绝对路径：{name}")
                if PureWindowsPath(name).drive:
                    raise UpdateError(f"压缩包包含非法盘符路径：{name}")
                normalized = name.replace("\\", "/")
                parts = PurePosixPath(normalized).parts
                if ".." in parts:
                    raise UpdateError(f"压缩包包含越界路径：{name}")
                if reject_symlinks:
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == _SYMLINK_MODE:
                        raise UpdateError(f"压缩包包含不允许的符号链接：{name}")
    except zipfile.BadZipFile as error:
        raise UpdateError("下载的更新压缩包已损坏，无法解压") from error


def extract_update_archive(
    archive_path: Path,
    extract_dir: Path,
    target_kind: str,
    *,
    cancel: threading.Event | None = None,
    verify_signature: bool = True,
) -> Path:
    if cancel and cancel.is_set():
        raise UpdateCancelled("更新已取消")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    resolved_root = extract_dir.resolve()

    if target_kind == "macos_app":
        validate_zip_members(archive_path, reject_symlinks=False)
        if cancel and cancel.is_set():
            raise UpdateCancelled("更新已取消")
        result = subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(archive_path), str(extract_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise UpdateError(f"解压 macOS 安装包失败：{detail or f'退出码 {result.returncode}'}")
        app_bundle = extract_dir / "Vannus.app"
        if not app_bundle.is_dir() or app_bundle.is_symlink():
            raise UpdateError("更新包内缺少有效的 Vannus.app")
        if not app_bundle.resolve().is_relative_to(resolved_root):
            raise UpdateError("更新包路径异常")
        executable = app_bundle / "Contents" / "MacOS" / "Vannus"
        if not executable.is_file():
            raise UpdateError("更新包内缺少主程序 Contents/MacOS/Vannus")
        if not os.access(executable, os.X_OK):
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        subprocess.run(
            ["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(app_bundle)],
            capture_output=True,
            check=False,
        )
        if verify_signature:
            verify = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app_bundle)],
                capture_output=True,
                text=True,
                check=False,
            )
            if verify.returncode != 0:
                detail = (verify.stderr or verify.stdout or "").strip()
                raise UpdateError(f"安装包签名校验失败：{detail or '应用包可能已损坏'}")
        return app_bundle

    if target_kind == "windows_dir":
        validate_zip_members(archive_path, reject_symlinks=True)
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                if cancel and cancel.is_set():
                    raise UpdateCancelled("更新已取消")
                destination = (extract_dir / info.filename).resolve()
                if not destination.is_relative_to(resolved_root):
                    raise UpdateError(f"压缩包路径越界：{info.filename}")
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=_CHUNK_SIZE)
        nested = extract_dir / "Vannus"
        if (nested / "Vannus.exe").is_file():
            return nested
        if (extract_dir / "Vannus.exe").is_file():
            return extract_dir
        raise UpdateError("更新包内缺少主程序 Vannus.exe")

    raise UpdateError(f"不支持的目标安装类型：{target_kind}")


_MACOS_UPDATER_SCRIPT = """#!/bin/sh
set -eu

APP_PID="$1"
TARGET_APP="$2"
STAGED_APP="$3"
BACKUP_APP="$4"
LOG_FILE="$5"
WORKSPACE_ARG="${6:-}"

mkdir -p "$(dirname "$LOG_FILE")"
log() {
    printf '%s %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG_FILE"
}

log "Starting macOS update: pid=$APP_PID target=$TARGET_APP"

waited=0
while kill -0 "$APP_PID" 2>/dev/null; do
    if [ "$waited" -ge 120 ]; then
        log "Timed out waiting for pid $APP_PID to exit"
        exit 1
    fi
    sleep 0.25
    waited=$((waited + 1))
done

rm -rf "$BACKUP_APP"
if [ -e "$TARGET_APP" ]; then
    if ! mv "$TARGET_APP" "$BACKUP_APP"; then
        log "Failed to move current app to backup: $BACKUP_APP"
        exit 1
    fi
fi

restore_backup() {
    log "Restoring previous app from backup"
    rm -rf "$TARGET_APP"
    if [ -e "$BACKUP_APP" ]; then
        mv "$BACKUP_APP" "$TARGET_APP" || true
    fi
}

if ! mv "$STAGED_APP" "$TARGET_APP" 2>/dev/null; then
    log "mv failed (possibly cross-volume); falling back to ditto"
    if ! /usr/bin/ditto "$STAGED_APP" "$TARGET_APP"; then
        restore_backup
        exit 1
    fi
    rm -rf "$STAGED_APP"
fi

if [ ! -x "$TARGET_APP/Contents/MacOS/Vannus" ]; then
    log "Updated executable missing or not executable"
    restore_backup
    exit 1
fi

/usr/bin/xattr -dr com.apple.quarantine "$TARGET_APP" 2>/dev/null || true
rm -rf "$BACKUP_APP"
STAGE_ROOT="$(dirname "$(dirname "$STAGED_APP")")"
if [ -d "$STAGE_ROOT" ]; then
    rm -rf "$STAGE_ROOT" || true
fi

log "Update applied successfully; relaunching $TARGET_APP"
if [ -n "$WORKSPACE_ARG" ]; then
    /usr/bin/open -n "$TARGET_APP" --args --workspace "$WORKSPACE_ARG"
else
    /usr/bin/open -n "$TARGET_APP"
fi
"""

_WINDOWS_UPDATER_SCRIPT = """param(
    [Parameter(Mandatory=$true)][int]$AppPid,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$StagedDir,
    [Parameter(Mandatory=$true)][string]$BackupDir,
    [Parameter(Mandatory=$true)][string]$LogFile,
    [string]$Workspace = ""
)

$ErrorActionPreference = "Stop"

$logParent = Split-Path -Parent $LogFile
if ($logParent -and -not (Test-Path -LiteralPath $logParent)) {
    New-Item -ItemType Directory -Path $logParent -Force | Out-Null
}

function Write-Log([string]$Message) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogFile -Value "$ts $Message" -Encoding UTF8
}

Write-Log "Starting Windows update: pid=$AppPid target=$InstallDir"

$waited = 0
while ($true) {
    $proc = Get-Process -Id $AppPid -ErrorAction SilentlyContinue
    if (-not $proc) { break }
    if ($waited -ge 120) {
        Write-Log "Timed out waiting for process $AppPid to exit"
        exit 1
    }
    Start-Sleep -Milliseconds 250
    $waited++
}
Start-Sleep -Milliseconds 500

if (Test-Path -LiteralPath $BackupDir) {
    Remove-Item -LiteralPath $BackupDir -Recurse -Force
}

$movedBackup = $false
for ($attempt = 1; $attempt -le 10; $attempt++) {
    try {
        if (Test-Path -LiteralPath $InstallDir) {
            Move-Item -LiteralPath $InstallDir -Destination $BackupDir -Force
        }
        $movedBackup = $true
        break
    } catch {
        Write-Log "Attempt $attempt to move install dir failed: $($_.Exception.Message)"
        Start-Sleep -Milliseconds 500
    }
}

if (-not $movedBackup) {
    Write-Log "Could not move current install directory to backup"
    exit 1
}

$tempNew = "$InstallDir.new"
function Restore-Backup {
    Write-Log "Restoring previous installation from backup"
    if (Test-Path -LiteralPath $InstallDir) {
        Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempNew) {
        Remove-Item -LiteralPath $tempNew -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $BackupDir) {
        Move-Item -LiteralPath $BackupDir -Destination $InstallDir -Force -ErrorAction SilentlyContinue
    }
}

try {
    $targetRoot = [System.IO.Path]::GetPathRoot($InstallDir)
    $stagedRoot = [System.IO.Path]::GetPathRoot($StagedDir)
    if ($targetRoot -eq $stagedRoot) {
        Move-Item -LiteralPath $StagedDir -Destination $InstallDir -Force
    } else {
        Write-Log "Cross-drive update detected ($stagedRoot -> $targetRoot); copying to temp dir"
        if (Test-Path -LiteralPath $tempNew) {
            Remove-Item -LiteralPath $tempNew -Recurse -Force
        }
        Copy-Item -LiteralPath $StagedDir -Destination $tempNew -Recurse -Force
        Move-Item -LiteralPath $tempNew -Destination $InstallDir -Force
    }
    $exePath = Join-Path $InstallDir "Vannus.exe"
    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "Updated executable missing: $exePath"
    }
} catch {
    Write-Log "Failed to place updated files: $($_.Exception.Message)"
    Restore-Backup
    exit 1
}

if (Test-Path -LiteralPath $BackupDir) {
    Remove-Item -LiteralPath $BackupDir -Recurse -Force -ErrorAction SilentlyContinue
}
$stageRoot = Split-Path -Parent (Split-Path -Parent $StagedDir)
if ($stageRoot -and (Test-Path -LiteralPath $stageRoot)) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$exePath = Join-Path $InstallDir "Vannus.exe"
Write-Log "Update applied successfully; relaunching $exePath"
if ($Workspace) {
    Start-Process -FilePath $exePath -ArgumentList @("--workspace", $Workspace)
} else {
    Start-Process -FilePath $exePath
}
"""


def write_updater_script(stage_dir: Path, target_kind: str) -> Path:
    stage_dir.mkdir(parents=True, exist_ok=True)
    if target_kind == "macos_app":
        script_path = stage_dir / "apply_update.sh"
        script_path.write_text(_MACOS_UPDATER_SCRIPT, encoding="utf-8", newline="\n")
        script_path.chmod(0o755)
        return script_path
    if target_kind == "windows_dir":
        script_path = stage_dir / "apply_update.ps1"
        script_path.write_text(_WINDOWS_UPDATER_SCRIPT, encoding="ascii", newline="\r\n")
        return script_path
    raise UpdateError(f"不支持的更新脚本类型：{target_kind}")


def launch_detached_updater(
    staged: StagedUpdate,
    target: InstallTarget,
    log_file: Path,
    workspace_arg: Path | None = None,
    *,
    pid: int | None = None,
) -> Path:
    if not target.can_auto_update or target.install_path is None:
        raise UpdateError(target.reason or "当前安装环境不支持自动替换更新")

    process_pid = os.getpid() if pid is None else int(pid)
    token = secrets.token_hex(4)
    install_path = target.install_path.resolve()
    backup_path = install_path.parent / f"{install_path.name}.bak-{token}"
    script_path = write_updater_script(staged.stage_dir.parent, target.kind)
    workspace_text = str(workspace_arg) if workspace_arg else ""

    if target.kind == "macos_app":
        cmd = [
            "/bin/sh",
            str(script_path),
            str(process_pid),
            str(install_path),
            str(staged.payload_path.resolve()),
            str(backup_path),
            str(log_file),
            workspace_text,
        ]
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return script_path

    if target.kind == "windows_dir":
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-AppPid",
            str(process_pid),
            "-InstallDir",
            str(install_path),
            "-StagedDir",
            str(staged.payload_path.resolve()),
            "-BackupDir",
            str(backup_path),
            "-LogFile",
            str(log_file),
        ]
        if workspace_text:
            cmd.extend(["-Workspace", workspace_text])
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
        )
        return script_path

    raise UpdateError(f"不支持的自动更新目标：{target.kind}")


def cleanup_stale_updates(updates_dir: Path, install_target: InstallTarget | None = None) -> None:
    if updates_dir.is_dir():
        for entry in list(updates_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("stage-"):
                shutil.rmtree(entry, ignore_errors=True)
            elif entry.is_file() and entry.name in ("apply_update.sh", "apply_update.ps1"):
                try:
                    entry.unlink(missing_ok=True)
                except OSError:
                    logging.debug("Skipping cleanup of %s", entry)

    if install_target and install_target.install_path is not None:
        install_path = install_target.install_path
        parent = install_path.parent
        prefix = f"{install_path.name}.bak-"
        if parent.is_dir():
            for entry in list(parent.iterdir()):
                if entry.name.startswith(prefix):
                    shutil.rmtree(entry, ignore_errors=True)
