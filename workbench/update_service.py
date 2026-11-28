"""Qt async coordinator for checking, downloading, verifying, and staging GitHub updates."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxy,
    QNetworkProxyFactory,
    QNetworkReply,
    QNetworkRequest,
)

from . import __version__
from .jobs import Job
from .updater import (
    LATEST_RELEASE_API_URL,
    InstallTarget,
    ReleaseInfo,
    StagedUpdate,
    UpdateCheckResult,
    UpdateError,
    apply_mirror_url,
    detect_install_target,
    evaluate_update,
    expected_sha256_for_asset,
    extract_update_archive,
    parse_github_release,
)

_API_TIMEOUT_MS = 15_000
_DOWNLOAD_TIMEOUT_MS = 30_000
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class UpdateService(QObject):
    """Manages GitHub Release checks and verified in-app update staging."""

    check_started = Signal()
    check_finished = Signal(object, bool)
    check_failed = Signal(str, bool)
    download_progress = Signal(int, int)
    stage_changed = Signal(str)
    update_ready = Signal(object)
    update_failed = Signal(str)
    update_cancelled = Signal()
    state_changed = Signal()

    def __init__(
        self,
        preferences,
        updates_dir: Path,
        *,
        current_version: str | None = None,
        api_url: str = LATEST_RELEASE_API_URL,
        install_target: InstallTarget | None = None,
        platform_name: str | None = None,
        machine: str | None = None,
        mirror_prefix: str | None = None,
        use_system_proxy: bool = True,
        verify_signature: bool = True,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.preferences = preferences
        self.updates_dir = Path(updates_dir)
        self.current_version = current_version or __version__
        self.api_url = api_url
        self.install_target = install_target or detect_install_target()
        self.platform_name = platform_name
        self.machine = machine
        self.mirror_prefix = (
            os.environ.get("VANNUS_UPDATE_MIRROR", "") if mirror_prefix is None else mirror_prefix
        )
        self.verify_signature = verify_signature

        self._nam = QNetworkAccessManager(self)
        if use_system_proxy and not _is_loopback_url(api_url):
            QNetworkProxyFactory.setUseSystemConfiguration(True)
        else:
            self._nam.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        self._check_reply: QNetworkReply | None = None
        self._check_manual = False
        self._download_reply: QNetworkReply | None = None
        self._extract_job: Job | None = None
        self._cancelling = False
        self._active_release: ReleaseInfo | None = None
        self._expected_sha256: str = ""
        self._stage_dir: Path | None = None
        self._part_path: Path | None = None
        self._part_handle = None
        self._hasher = None
        self._bytes_received = 0
        self._bytes_total = 0

        self.last_result: UpdateCheckResult | None = None
        self.last_error: str = ""

    @property
    def checking(self) -> bool:
        return self._check_reply is not None

    @property
    def downloading(self) -> bool:
        return self._download_reply is not None or self._extract_job is not None

    @property
    def auto_check(self) -> bool:
        return bool(self.preferences.value("updates/auto_check", True, type=bool))

    def set_auto_check(self, enabled: bool) -> None:
        self.preferences.setValue("updates/auto_check", bool(enabled))
        self.preferences.sync()
        self.state_changed.emit()

    @property
    def ignored_version(self) -> str:
        return str(self.preferences.value("updates/ignored_version", "", type=str) or "").strip()

    def ignore_version(self, version_text: str) -> None:
        self.preferences.setValue("updates/ignored_version", str(version_text or "").strip())
        self.preferences.sync()
        self.state_changed.emit()

    def clear_ignored_version(self) -> None:
        self.preferences.remove("updates/ignored_version")
        self.preferences.sync()
        self.state_changed.emit()

    def is_version_ignored(self, release: ReleaseInfo | None) -> bool:
        if release is None:
            return False
        ignored = self.ignored_version
        return bool(ignored) and ignored in (release.version.display, release.tag_name)

    def has_unignored_update(self) -> bool:
        result = self.last_result
        return bool(
            result
            and result.update_available
            and result.latest_release is not None
            and not self.is_version_ignored(result.latest_release)
        )

    def _cache_path(self) -> Path:
        return self.updates_dir / "release-cache.json"

    def _load_cache(self) -> dict:
        path = self._cache_path()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("url") != self.api_url:
            return {}
        return data

    def _save_cache(self, etag: str, payload: dict) -> None:
        try:
            self.updates_dir.mkdir(parents=True, exist_ok=True)
            cache = {"url": self.api_url, "etag": etag, "payload": payload}
            self._cache_path().write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logging.debug("Failed to persist update ETag cache", exc_info=True)

    def _build_request(self, url: str, *, timeout_ms: int, is_api: bool = False) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setMaximumRedirectsAllowed(10)
        request.setTransferTimeout(timeout_ms)
        request.setRawHeader(b"User-Agent", f"Vannus/{self.current_version}".encode("utf-8"))
        if is_api:
            request.setRawHeader(b"Accept", b"application/vnd.github+json")
            request.setRawHeader(b"X-GitHub-Api-Version", b"2022-11-28")
        return request

    def check_for_updates(self, *, manual: bool = False) -> bool:
        if self._check_reply is not None:
            if manual:
                self._check_manual = True
            return False

        self._check_manual = manual
        self.last_error = ""
        request = self._build_request(self.api_url, timeout_ms=_API_TIMEOUT_MS, is_api=True)
        cache = self._load_cache()
        etag = str(cache.get("etag") or "").strip()
        if etag and isinstance(cache.get("payload"), dict):
            request.setRawHeader(b"If-None-Match", etag.encode("utf-8"))

        reply = self._nam.get(request)
        self._check_reply = reply
        reply.finished.connect(lambda r=reply: self._on_check_finished(r))
        self.check_started.emit()
        self.state_changed.emit()
        return True

    def _on_check_finished(self, reply: QNetworkReply) -> None:
        manual = self._check_manual
        self._check_reply = None
        try:
            status = _http_status(reply)
            if status == 304:
                cache = self._load_cache()
                payload = cache.get("payload")
                if not isinstance(payload, dict):
                    raise UpdateError("更新缓存数据无效，请稍后重试")
            elif reply.error() != QNetworkReply.NetworkError.NoError or status >= 400:
                raise UpdateError(_describe_network_error(reply, status, is_api=True))
            else:
                raw = bytes(reply.readAll())
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as error:
                    raise UpdateError("无法解析 GitHub Release 响应数据") from error
                etag = _header_value(reply, "ETag")
                if etag and isinstance(payload, dict):
                    self._save_cache(etag, payload)

            release = parse_github_release(
                payload,
                platform_name=self.platform_name,
                machine=self.machine,
            )
            result = evaluate_update(self.current_version, release, self.install_target)
            self.last_result = result
            self.last_error = ""
            if manual and result.update_available and self.is_version_ignored(result.latest_release):
                self.clear_ignored_version()
            self.state_changed.emit()
            self.check_finished.emit(result, manual)
        except Exception as error:
            message = str(error) or "检查更新失败"
            self.last_error = message
            logging.info("Update check failed (manual=%s): %s", manual, message)
            self.state_changed.emit()
            self.check_failed.emit(message, manual)
        finally:
            reply.deleteLater()

    def start_download(self, release: ReleaseInfo) -> bool:
        if self.downloading:
            return False
        if release.asset is None:
            self.update_failed.emit("该版本没有适配当前平台的安装包")
            return False
        if release.checksums_asset is None:
            self.update_failed.emit("该版本缺少 SHA256SUMS.txt 校验文件，无法安全自动安装")
            return False

        self._cancelling = False
        self._active_release = release
        self._expected_sha256 = ""
        self._bytes_received = 0
        self._bytes_total = release.asset.size
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        self._stage_dir = self.updates_dir / f"stage-{release.version.display}-{secrets.token_hex(4)}"
        self._stage_dir.mkdir(parents=True, exist_ok=True)

        checksums_url = apply_mirror_url(release.checksums_asset.download_url, self.mirror_prefix)
        request = self._build_request(checksums_url, timeout_ms=_API_TIMEOUT_MS)
        reply = self._nam.get(request)
        self._download_reply = reply
        reply.finished.connect(lambda r=reply: self._on_checksums_finished(r))
        self.stage_changed.emit("checksum")
        self.state_changed.emit()
        return True

    def _on_checksums_finished(self, reply: QNetworkReply) -> None:
        self._download_reply = None
        release = self._active_release
        try:
            if self._cancelling or reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
                self._finish_cancelled()
                return
            status = _http_status(reply)
            if reply.error() != QNetworkReply.NetworkError.NoError or status >= 400:
                raise UpdateError(f"下载校验文件失败：{_describe_network_error(reply, status)}")
            if release is None or release.asset is None or self._stage_dir is None:
                raise UpdateError("更新上下文已失效")
            text = bytes(reply.readAll()).decode("utf-8", errors="replace")
            self._expected_sha256 = expected_sha256_for_asset(text, release.asset.name)
            self._begin_archive_download(release)
        except Exception as error:
            self._finish_failed(str(error) or "获取校验文件失败")
        finally:
            reply.deleteLater()

    def _begin_archive_download(self, release: ReleaseInfo) -> None:
        assert release.asset is not None
        assert self._stage_dir is not None
        self._part_path = self._stage_dir / f"{release.asset.name}.part"
        self._part_handle = self._part_path.open("wb")
        self._hasher = hashlib.sha256()
        self._bytes_received = 0
        self._bytes_total = release.asset.size

        download_url = apply_mirror_url(release.asset.download_url, self.mirror_prefix)
        request = self._build_request(download_url, timeout_ms=_DOWNLOAD_TIMEOUT_MS)
        reply = self._nam.get(request)
        self._download_reply = reply
        reply.readyRead.connect(lambda r=reply: self._on_archive_ready_read(r))
        reply.downloadProgress.connect(self._on_archive_progress)
        reply.finished.connect(lambda r=reply: self._on_archive_finished(r))
        self.stage_changed.emit("downloading")
        self.download_progress.emit(0, self._bytes_total)

    def _on_archive_ready_read(self, reply: QNetworkReply) -> None:
        if self._cancelling or self._part_handle is None or self._hasher is None:
            return
        chunk = bytes(reply.readAll())
        if not chunk:
            return
        try:
            self._part_handle.write(chunk)
            self._hasher.update(chunk)
            self._bytes_received += len(chunk)
        except OSError as error:
            logging.exception("Failed writing update download chunk")
            self._close_part_handle()
            self._cancelling = False
            reply.abort()
            self._finish_failed(f"写入临时安装包失败：{error}")

    def _on_archive_progress(self, received: int, total: int) -> None:
        if self._cancelling:
            return
        effective_total = total if total > 0 else self._bytes_total
        self.download_progress.emit(received, effective_total)

    def _on_archive_finished(self, reply: QNetworkReply) -> None:
        self._download_reply = None
        release = self._active_release
        try:
            if not self._cancelling and self._part_handle is not None and self._hasher is not None:
                tail = bytes(reply.readAll())
                if tail:
                    self._part_handle.write(tail)
                    self._hasher.update(tail)
                    self._bytes_received += len(tail)
            self._close_part_handle()

            if self._cancelling or reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
                self._finish_cancelled()
                return
            status = _http_status(reply)
            if reply.error() != QNetworkReply.NetworkError.NoError or status >= 400:
                raise UpdateError(f"下载安装包失败：{_describe_network_error(reply, status)}")
            if release is None or release.asset is None or self._stage_dir is None or self._part_path is None:
                raise UpdateError("更新下载上下文已失效")
            if self._bytes_received <= 0:
                raise UpdateError("下载的安装包为空")

            self.stage_changed.emit("verifying")
            actual_sha256 = self._hasher.hexdigest().lower() if self._hasher is not None else ""
            if not hmac.compare_digest(actual_sha256, self._expected_sha256):
                raise UpdateError("安装包 SHA256 校验失败，文件可能已损坏或被篡改")

            archive_path = self._stage_dir / release.asset.name
            self._part_path.replace(archive_path)
            self._part_path = None
            self._begin_extraction(release, archive_path, actual_sha256)
        except Exception as error:
            self._finish_failed(str(error) or "下载安装包失败")
        finally:
            reply.deleteLater()

    def _begin_extraction(self, release: ReleaseInfo, archive_path: Path, sha256: str) -> None:
        assert self._stage_dir is not None
        extract_dir = self._stage_dir / "extracted"
        target_kind = self.install_target.kind
        verify_sig = self.verify_signature
        stage_dir = self._stage_dir

        def work(cancel, _progress):
            payload = extract_update_archive(
                archive_path,
                extract_dir,
                target_kind,
                cancel=cancel,
                verify_signature=verify_sig,
            )
            return StagedUpdate(
                release=release,
                stage_dir=stage_dir,
                archive_path=archive_path,
                payload_path=payload,
                sha256=sha256,
            )

        job = Job(work)
        self._extract_job = job
        job.signals.succeeded.connect(self._on_extract_succeeded)
        job.signals.failed.connect(self._on_extract_failed)
        self.stage_changed.emit("extracting")
        self._pool.start(job)

    def _on_extract_succeeded(self, staged: StagedUpdate) -> None:
        self._extract_job = None
        if self._cancelling:
            self._finish_cancelled()
            return
        self._active_release = None
        self._stage_dir = None
        self.stage_changed.emit("ready")
        self.state_changed.emit()
        self.update_ready.emit(staged)

    def _on_extract_failed(self, message: str) -> None:
        self._extract_job = None
        if self._cancelling or message == "更新已取消":
            self._finish_cancelled()
            return
        self._finish_failed(message or "解压安装包失败")

    def cancel_download(self) -> None:
        if not self.downloading:
            return
        self._cancelling = True
        self._close_part_handle()
        if self._download_reply is not None:
            self._download_reply.abort()
        if self._extract_job is not None:
            self._extract_job.cancel.set()

    def shutdown(self) -> None:
        if self._check_reply is not None:
            self._check_reply.abort()
        if self.downloading:
            self.cancel_download()
        self._nam.clearConnectionCache()
        self._pool.waitForDone(3000)

    def _close_part_handle(self) -> None:
        if self._part_handle is not None:
            try:
                self._part_handle.flush()
                self._part_handle.close()
            except OSError:
                pass
            self._part_handle = None

    def _cleanup_current_stage(self) -> None:
        self._close_part_handle()
        if self._stage_dir is not None and self._stage_dir.exists():
            shutil.rmtree(self._stage_dir, ignore_errors=True)
        self._stage_dir = None
        self._part_path = None
        self._hasher = None
        self._active_release = None
        self._expected_sha256 = ""

    def _finish_cancelled(self) -> None:
        self._cancelling = False
        self._cleanup_current_stage()
        self.state_changed.emit()
        self.update_cancelled.emit()

    def _finish_failed(self, message: str) -> None:
        self._cancelling = False
        self._cleanup_current_stage()
        self.state_changed.emit()
        self.update_failed.emit(message)


def _is_loopback_url(url: str) -> bool:
    host = QUrl(url).host().lower()
    return host in _LOOPBACK_HOSTS


def _http_status(reply: QNetworkReply) -> int:
    code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    return int(code) if code is not None else 0


def _header_value(reply: QNetworkReply, name: str) -> str:
    raw = bytes(reply.rawHeader(name))
    return raw.decode("utf-8", errors="ignore").strip()


def _describe_network_error(reply: QNetworkReply, status: int, *, is_api: bool = False) -> str:
    if status == 403 and is_api and _header_value(reply, "X-RateLimit-Remaining") == "0":
        return "GitHub API 请求达到频率上限，请稍后再试或直接打开发布页"
    if status == 404 and is_api:
        return "GitHub 仓库中尚未找到已发布的 Release 版本"
    if status >= 400:
        return f"服务器返回 HTTP {status}"
    err = reply.error()
    if err == QNetworkReply.NetworkError.TimeoutError:
        return "连接 GitHub 超时，请检查网络或代理设置"
    if err in (
        QNetworkReply.NetworkError.HostNotFoundError,
        QNetworkReply.NetworkError.ConnectionRefusedError,
        QNetworkReply.NetworkError.NetworkSessionFailedError,
        QNetworkReply.NetworkError.TemporaryNetworkFailureError,
    ):
        return "无法连接到 GitHub，请检查网络连接"
    if err == QNetworkReply.NetworkError.SslHandshakeFailedError:
        return "TLS 安全连接失败，请检查系统时间或网络代理证书"
    return reply.errorString() or "网络请求失败"
