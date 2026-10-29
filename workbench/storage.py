"""Transactional local archive. No Qt dependencies; each operation owns its connection."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import threading
import uuid
import zipfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from PIL import Image, ImageOps

SCHEMA_VERSION = 2
IMAGE_LIMIT = 100_000_000
Image.MAX_IMAGE_PIXELS = IMAGE_LIMIT


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid() -> str:
    return uuid.uuid4().hex


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def check_name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError("名称不能为空，且不能超过 200 个字符")
    return value


def copy_hashed(source, target, cancel):
    """Stream large media files with bounded memory and cancellation."""
    digest = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        if cancel.is_set():
            raise Cancelled("已取消文件传输")
        target.write(chunk)
        digest.update(chunk)
    return digest.hexdigest()


def check_regions(regions, width: int, height: int) -> list[dict]:
    result = []
    names = set()
    for region in regions:
        name = check_name(region["name"])
        x, y, w, h = (int(region[k]) for k in ("x", "y", "w", "h"))
        if name in names:
            raise ValueError("热区名称不能重复")
        if min(x, y) < 0 or min(w, h) < 2 or x + w > width or y + h > height:
            raise ValueError(f"热区「{name}」超出图像边界或尺寸过小")
        names.add(name)
        result.append(dict(name=name, x=x, y=y, w=w, h=h))
    return result


class Cancelled(Exception):
    pass


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "archive.sqlite3"
        self._write_lock = threading.RLock()
        for name in ("images", "videos", ".staging", ".thumbnails"):
            (self.root / name).mkdir(exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("工作区由更新版本创建，请升级应用")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, archived INTEGER DEFAULT 0,
                    deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    name TEXT NOT NULL, defaults TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS photos (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    batch_id TEXT NOT NULL REFERENCES batches(id), created_at TEXT NOT NULL,
                    sample_id TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, original TEXT NOT NULL,
                    processing TEXT NOT NULL DEFAULT '[]', deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS templates (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    name TEXT NOT NULL, version INTEGER NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL,
                    regions TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    batch_id TEXT NOT NULL REFERENCES batches(id), created_at TEXT NOT NULL,
                    sample_id TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL, original TEXT NOT NULL, deleted_at TEXT);
                CREATE INDEX IF NOT EXISTS video_dataset_time ON videos(dataset_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS photo_dataset_time ON photos(dataset_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS photo_batch ON photos(batch_id);
                CREATE INDEX IF NOT EXISTS batch_dataset ON batches(dataset_id);
            """)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.recover()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def path(self, relative: str) -> Path:
        result = (self.root / relative).resolve()
        if not result.is_relative_to(self.root):
            raise ValueError("文件路径超出工作区")
        return result

    def recover(self):
        # Only called at startup, after acquiring the workspace's process lock.
        with self.connect() as db:
            live = {r[0] for r in db.execute("SELECT id FROM photos")}
            live_videos = {r[0] for r in db.execute("SELECT original FROM videos")}
        for p in (self.root / ".staging").iterdir():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        for p in (self.root / "images").iterdir():
            if p.is_dir() and p.name not in live:
                shutil.rmtree(p)
        for p in (self.root / "videos").glob("*.mp4"):
            if p.relative_to(self.root).as_posix() not in live_videos:
                p.unlink()

    def new_recording_path(self):
        folder = self.root / ".staging" / uid()
        folder.mkdir()
        return folder / "recording.mp4"

    def add_video(
        self, source, dataset_id, batch_id, *, duration_ms, sample_id="", tags="", note="", metadata=None
    ):
        source = Path(source).resolve()
        if not source.is_relative_to(self.root / ".staging") or source.suffix != ".mp4":
            raise ValueError("视频必须来自当前工作区的录制暂存目录")
        if not source.is_file() or not source.stat().st_size or duration_ms <= 0:
            raise ValueError("视频文件为空或时长无效")
        video_id = uid()
        relative = f"videos/{video_id}.mp4"
        final = self.path(relative)
        with self._write_lock:
            try:
                with self.connect() as db:
                    self._active_batch(db, dataset_id, batch_id)
                    os.replace(source, final)
                    db.execute(
                        """INSERT INTO videos(id,dataset_id,batch_id,created_at,sample_id,tags,note,
                        metadata,duration_ms,original) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (
                            video_id,
                            dataset_id,
                            batch_id,
                            now(),
                            sample_id,
                            tags,
                            note,
                            dump(metadata or {}),
                            duration_ms,
                            relative,
                        ),
                    )
            except BaseException:
                if final.exists():
                    os.replace(final, source)
                raise
        try:
            source.parent.rmdir()
        except OSError:
            pass  # Directory cleanup must not turn a committed capture into a failure.
        return video_id

    def videos(self, *, dataset_id=None, batch_id=None, search="", date_from="", date_to=""):
        sql, args = self._query(dataset_id, batch_id, search, date_from, date_to)
        sql = sql.replace("FROM photos p", "FROM videos p")
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT p.*,b.name AS batch_name,d.name AS dataset_name "
                    + sql
                    + " ORDER BY p.created_at DESC,p.id DESC",
                    args,
                )
            ]

    def datasets(self, include_archived=True):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    """
                SELECT d.*, (SELECT COUNT(*) FROM videos v JOIN batches b ON b.id=v.batch_id
                WHERE v.dataset_id=d.id AND v.deleted_at IS NULL AND b.deleted_at IS NULL) AS video_count,
                (SELECT COUNT(*) FROM photos p JOIN batches b ON b.id=p.batch_id
                WHERE p.dataset_id=d.id AND p.deleted_at IS NULL AND b.deleted_at IS NULL) AS count,
                (SELECT p.original FROM photos p JOIN batches b ON b.id=p.batch_id
                WHERE p.dataset_id=d.id AND p.deleted_at IS NULL AND b.deleted_at IS NULL
                ORDER BY p.created_at DESC LIMIT 1) AS cover
                FROM datasets d WHERE d.deleted_at IS NULL AND (? OR d.archived=0)
                ORDER BY d.created_at DESC
            """,
                    (int(include_archived),),
                )
            ]

    def save_dataset(self, name, description="", tags="", dataset_id=None):
        name = check_name(name)
        with self._write_lock, self.connect() as db:
            if dataset_id:
                db.execute(
                    "UPDATE datasets SET name=?,description=?,tags=? WHERE id=?",
                    (name, description, tags, dataset_id),
                )
            else:
                dataset_id = uid()
                db.execute(
                    "INSERT INTO datasets(id,name,description,tags,created_at) VALUES(?,?,?,?,?)",
                    (dataset_id, name, description, tags, now()),
                )
        return dataset_id

    def archive_dataset(self, dataset_id, archived):
        with self._write_lock, self.connect() as db:
            db.execute("UPDATE datasets SET archived=? WHERE id=?", (int(archived), dataset_id))

    def batches(self, dataset_id):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM batches WHERE dataset_id=? AND deleted_at IS NULL ORDER BY created_at DESC",
                    (dataset_id,),
                )
            ]

    def save_batch(self, dataset_id, name, defaults=None, batch_id=None):
        name = check_name(name)
        with self._write_lock, self.connect() as db:
            if batch_id:
                db.execute(
                    "UPDATE batches SET name=?, defaults=? WHERE id=? AND dataset_id=?",
                    (name, dump(defaults or {}), batch_id, dataset_id),
                )
            else:
                batch_id = uid()
                db.execute(
                    "INSERT INTO batches(id,dataset_id,name,defaults,created_at) VALUES(?,?,?,?,?)",
                    (batch_id, dataset_id, name, dump(defaults or {}), now()),
                )
        return batch_id

    def _active_batch(self, db, dataset_id, batch_id):
        row = db.execute(
            """SELECT b.id FROM batches b JOIN datasets d ON d.id=b.dataset_id
            WHERE b.id=? AND b.dataset_id=? AND b.deleted_at IS NULL
            AND d.deleted_at IS NULL AND d.archived=0""",
            (batch_id, dataset_id),
        ).fetchone()
        if row is None:
            raise ValueError("请选择未归档数据集中的有效批次")

    def templates(self, dataset_id):
        with self.connect() as db:
            return [
                dict(r) | {"regions": json.loads(r["regions"])}
                for r in db.execute(
                    "SELECT * FROM templates WHERE dataset_id=? ORDER BY created_at DESC", (dataset_id,)
                )
            ]

    def save_template(self, dataset_id, name, width, height, regions):
        name = check_name(name)
        regions = check_regions(regions, width, height)
        if not regions:
            raise ValueError("请先绘制至少一个热区")
        with self._write_lock, self.connect() as db:
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM templates WHERE dataset_id=? AND name=?",
                (dataset_id, name),
            ).fetchone()[0]
            template_id = uid()
            db.execute(
                "INSERT INTO templates VALUES(?,?,?,?,?,?,?,?)",
                (template_id, dataset_id, name, version, width, height, dump(regions), now()),
            )
        return dict(
            id=template_id,
            dataset_id=dataset_id,
            name=name,
            version=version,
            width=width,
            height=height,
            regions=regions,
        )

    def _processing(self, image, regions, destination, relative, template, version=1, *, compress_level=6):
        regions = check_regions(regions, *image.size)
        crops = []
        directory = destination / f"v{version}"
        directory.mkdir()
        for index, region in enumerate(regions):
            name = f"{index + 1:03}.png"
            x, y, w, h = (region[k] for k in ("x", "y", "w", "h"))
            image.crop((x, y, x + w, y + h)).save(directory / name, "PNG", compress_level=compress_level)
            crops.append(dict(region=region, path=f"{relative}/v{version}/{name}"))
        return dict(version=version, created_at=now(), template=template, crops=crops)

    def add_photo(
        self,
        source,
        dataset_id,
        batch_id,
        *,
        sample_id="",
        tags="",
        note="",
        custom=None,
        camera=None,
        regions=None,
        template=None,
        png_compress_level=6,
    ):
        with self._write_lock:
            with self.connect() as db:
                self._active_batch(db, dataset_id, batch_id)
            if shutil.disk_usage(self.root).free < 100 * 1024 * 1024:
                raise OSError("工作区剩余空间不足 100 MB，请清理磁盘")
            photo_id = uid()
            relative = f"images/{photo_id}"
            staging = self.root / ".staging" / photo_id
            final = self.root / relative
            staging.mkdir()
            try:
                if isinstance(source, Image.Image):
                    image = ImageOps.exif_transpose(source).convert("RGB")
                else:
                    with Image.open(source) as opened:
                        image = ImageOps.exif_transpose(opened).convert("RGB")
                if image.width * image.height > IMAGE_LIMIT:
                    raise ValueError("图像超过一亿像素")
                image.save(staging / "original.png", "PNG", compress_level=png_compress_level)
                history = []
                if regions:
                    history.append(
                        self._processing(
                            image, regions, staging, relative, template, compress_level=png_compress_level
                        )
                    )
                metadata = dict(
                    custom=custom or {},
                    camera=camera or {},
                    source_name=Path(source).name if isinstance(source, (str, Path)) else "camera",
                )
                with self.connect() as db:
                    self._active_batch(db, dataset_id, batch_id)
                    os.replace(staging, final)
                    db.execute(
                        """INSERT INTO photos(id,dataset_id,batch_id,created_at,sample_id,tags,note,
                        metadata,width,height,original,processing) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            photo_id,
                            dataset_id,
                            batch_id,
                            now(),
                            sample_id,
                            tags,
                            note,
                            dump(metadata),
                            image.width,
                            image.height,
                            f"{relative}/original.png",
                            dump(history),
                        ),
                    )
                return photo_id
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                shutil.rmtree(final, ignore_errors=True)
                raise

    @staticmethod
    def _query(dataset_id=None, batch_id=None, search="", date_from="", date_to=""):
        sql = """FROM photos p JOIN datasets d ON p.dataset_id=d.id JOIN batches b ON p.batch_id=b.id
            WHERE p.deleted_at IS NULL AND d.deleted_at IS NULL AND b.deleted_at IS NULL"""
        args = []
        for value, clause in (
            (dataset_id, "p.dataset_id=?"),
            (batch_id, "p.batch_id=?"),
            (date_from, "p.created_at>=?"),
            (date_to, "p.created_at<?"),
        ):
            if value:
                sql += " AND " + clause
                args.append(value)
        if search.strip():
            sql += " AND (p.sample_id LIKE ? ESCAPE '\\' OR p.tags LIKE ? ESCAPE '\\' OR p.note LIKE ? ESCAPE '\\')"
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.extend([f"%{escaped}%"] * 3)
        return sql, args

    def photos(self, *, offset=0, limit=100, **filters):
        sql, args = self._query(**filters)
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT p.*,b.name AS batch_name,d.name AS dataset_name "
                    + sql
                    + " ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            ]

    def count_photos(self, **filters):
        sql, args = self._query(**filters)
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) " + sql, args).fetchone()[0]

    def _media_query(self, *, media_type="all", **filters):
        if media_type not in {"all", "photo", "video"}:
            raise ValueError("无效的素材类型")
        sql, args = self._query(**filters)
        fields = (
            "p.id,p.dataset_id,p.batch_id,p.created_at,p.sample_id,p.tags,p.note,"
            "p.metadata,p.original,b.name AS batch_name,d.name AS dataset_name"
        )
        queries, parameters = [], []
        if media_type in {"all", "photo"}:
            queries.append(
                f"SELECT {fields},'photo' AS media_type,p.width,p.height,p.processing,"
                "NULL AS duration_ms " + sql
            )
            parameters.extend(args)
        if media_type in {"all", "video"}:
            queries.append(
                f"SELECT {fields},'video' AS media_type,NULL AS width,NULL AS height,"
                "'[]' AS processing,p.duration_ms " + sql.replace("FROM photos p", "FROM videos p")
            )
            parameters.extend(args)
        return " UNION ALL ".join(queries), parameters

    def media(self, *, offset=0, limit=100, **filters):
        sql, args = self._media_query(**filters)
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    f"SELECT * FROM ({sql}) ORDER BY created_at DESC,id DESC,media_type LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            ]

    def media_counts(self, **filters):
        sql, args = self._media_query(**filters)
        with self.connect() as db:
            return {"photo": 0, "video": 0} | dict(
                db.execute(f"SELECT media_type,COUNT(*) FROM ({sql}) GROUP BY media_type", args)
            )

    def photo(self, photo_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            raise ValueError("图片已被删除")
        return dict(row)

    def update_photos(self, photo_ids, **changes):
        allowed = {"sample_id", "tags", "note", "custom"}
        if not changes or not changes.keys() <= allowed:
            raise ValueError("无效的图片字段")
        with self._write_lock, self.connect() as db:
            custom = changes.pop("custom", None)
            if changes:
                db.executemany(
                    "UPDATE photos SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
                    [(*changes.values(), photo_id) for photo_id in photo_ids],
                )
            if custom is not None:
                if not isinstance(custom, dict) or not all(isinstance(v, str) for v in custom.values()):
                    raise ValueError("自定义信息必须为文本字段")
                for photo_id in photo_ids:
                    record = db.execute("SELECT metadata FROM photos WHERE id=?", (photo_id,)).fetchone()
                    if record:
                        metadata = json.loads(record[0])
                        metadata["custom"] = custom
                        db.execute("UPDATE photos SET metadata=? WHERE id=?", (dump(metadata), photo_id))

    def move_photos(self, photo_ids, dataset_id, batch_id):
        with self._write_lock, self.connect() as db:
            self._active_batch(db, dataset_id, batch_id)
            db.executemany(
                "UPDATE photos SET dataset_id=?,batch_id=? WHERE id=?",
                [(dataset_id, batch_id, i) for i in photo_ids],
            )

    def reprocess(self, photo_id, template):
        with self._write_lock:
            photo = self.photo(photo_id)
            if (photo["width"], photo["height"]) != (template["width"], template["height"]):
                raise ValueError("图片尺寸与模板不匹配，请重新标定")
            history = json.loads(photo["processing"])
            version = max((p["version"] for p in history), default=0) + 1
            staging = self.root / ".staging" / uid()
            staging.mkdir()
            relative = f"images/{photo_id}"
            final = self.path(f"{relative}/v{version}")
            try:
                with Image.open(self.path(photo["original"])) as image:
                    processing = self._processing(
                        image, template["regions"], staging, relative, template, version
                    )
                # An interrupted reprocess may have left an uncommitted version directory.
                shutil.rmtree(final, ignore_errors=True)
                os.replace(staging / f"v{version}", final)
                history.append(processing)
                with self.connect() as db:
                    db.execute("UPDATE photos SET processing=? WHERE id=?", (dump(history), photo_id))
            except BaseException:
                shutil.rmtree(final, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    def trash(self, kind, ids):
        table = {"dataset": "datasets", "batch": "batches", "photo": "photos", "video": "videos"}[kind]
        with self._write_lock, self.connect() as db:
            db.executemany(f"UPDATE {table} SET deleted_at=? WHERE id=?", [(now(), i) for i in ids])

    def trash_items(self):
        with self.connect() as db:
            result = []
            for kind, table, label in (
                ("dataset", "datasets", "name"),
                ("batch", "batches", "name"),
                ("photo", "photos", "sample_id"),
                ("video", "videos", "sample_id"),
            ):
                result.extend(
                    dict(r) | {"kind": kind}
                    for r in db.execute(
                        f"SELECT id,{label} AS name,deleted_at FROM {table} WHERE deleted_at IS NOT NULL"
                    )
                )
            return sorted(result, key=lambda r: r["deleted_at"], reverse=True)

    def restore(self, kind, record_id):
        table = {"dataset": "datasets", "batch": "batches", "photo": "photos", "video": "videos"}[kind]
        with self._write_lock, self.connect() as db:
            if kind in ("photo", "video"):
                row = db.execute(
                    f"SELECT dataset_id,batch_id FROM {table} WHERE id=?", (record_id,)
                ).fetchone()
                if row:
                    db.execute("UPDATE batches SET deleted_at=NULL WHERE id=?", (row["batch_id"],))
                    db.execute("UPDATE datasets SET deleted_at=NULL WHERE id=?", (row["dataset_id"],))
            elif kind == "batch":
                db.execute(
                    "UPDATE datasets SET deleted_at=NULL WHERE id=(SELECT dataset_id FROM batches WHERE id=?)",
                    (record_id,),
                )
            db.execute(f"UPDATE {table} SET deleted_at=NULL WHERE id=?", (record_id,))

    def empty_trash(self):
        with self._write_lock, self.connect() as db:
            ids = [
                r[0]
                for r in db.execute("""SELECT p.id FROM photos p JOIN datasets d ON d.id=p.dataset_id
                JOIN batches b ON b.id=p.batch_id WHERE p.deleted_at IS NOT NULL
                OR d.deleted_at IS NOT NULL OR b.deleted_at IS NOT NULL""")
            ]
            db.executemany("DELETE FROM photos WHERE id=?", [(i,) for i in ids])
            videos = list(
                db.execute("""SELECT p.id,p.original FROM videos p
                JOIN datasets d ON d.id=p.dataset_id JOIN batches b ON b.id=p.batch_id
                WHERE p.deleted_at IS NOT NULL OR d.deleted_at IS NOT NULL OR b.deleted_at IS NOT NULL""")
            )
            db.executemany("DELETE FROM videos WHERE id=?", [(r["id"],) for r in videos])
            db.execute(
                "DELETE FROM templates WHERE dataset_id IN (SELECT id FROM datasets WHERE deleted_at IS NOT NULL)"
            )
            db.execute(
                "DELETE FROM batches WHERE deleted_at IS NOT NULL OR dataset_id IN (SELECT id FROM datasets WHERE deleted_at IS NOT NULL)"
            )
            db.execute("DELETE FROM datasets WHERE deleted_at IS NOT NULL")
        for i in ids:
            shutil.rmtree(self.root / "images" / i, ignore_errors=True)
        for video in videos:
            self.path(video["original"]).unlink(missing_ok=True)

    def export_zip(
        self,
        destination,
        dataset_id,
        *,
        filters=None,
        crops_only=False,
        cancel: threading.Event | None = None,
        progress: Callable | None = None,
    ):
        cancel = cancel or threading.Event()
        destination = Path(destination)
        temporary = destination.with_name(destination.name + "." + uid() + ".partial")
        # Serializing archive-changing work yields a coherent snapshot even during recropping or deletion.
        with self._write_lock:
            with self.connect() as db:
                ds = db.execute(
                    "SELECT * FROM datasets WHERE id=? AND deleted_at IS NULL", (dataset_id,)
                ).fetchone()
                if ds is None:
                    raise ValueError("数据集不存在")
            filters = dict(filters or {})
            media_type = filters.pop("media_type", "all")
            if media_type not in {"all", "photo", "video"}:
                raise ValueError("无效的素材类型")
            photos = [] if media_type == "video" else self.photos(dataset_id=dataset_id, limit=-1, **filters)
            videos = (
                [] if crops_only or media_type == "photo" else self.videos(dataset_id=dataset_id, **filters)
            )
            if not photos and not videos:
                raise ValueError("当前范围没有可导出的图片或视频")
            total = len(photos) + len(videos)
            batches = self.batches(dataset_id)
            manifest = dict(
                format="fieldwork",
                version=2 if videos else 1,
                complete=not crops_only,
                exported_at=now(),
                dataset=dict(ds),
                batches=batches,
                templates=self.templates(dataset_id),
                photos=photos,
                videos=videos,
                files={},
            )
            try:
                with zipfile.ZipFile(
                    temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True
                ) as archive:
                    csv_file = io.StringIO(newline="")
                    writer = csv.writer(csv_file)
                    writer.writerow(
                        [
                            "photo_id",
                            "sample_id",
                            "batch",
                            "created_at",
                            "tags",
                            "note",
                            "custom",
                            "original",
                            "crops",
                        ]
                    )
                    for index, photo in enumerate(photos):
                        if cancel.is_set():
                            raise Cancelled("已取消导出")
                        history = json.loads(photo["processing"])
                        paths = [] if crops_only else [photo["original"]]
                        # Full backups preserve every processing version.
                        paths.extend(c["path"] for p in history for c in p["crops"])
                        for path in paths:
                            if cancel.is_set():
                                raise Cancelled("已取消导出")
                            data = self.path(path).read_bytes()
                            parts = path.split("/")
                            export_path = (
                                ("originals/" + parts[1] + ".png")
                                if parts[-1] == "original.png"
                                else "crops/" + "/".join(parts[1:])
                            )
                            archive.writestr(export_path, data)
                            manifest["files"][path] = dict(
                                path=export_path, sha256=hashlib.sha256(data).hexdigest()
                            )
                        writer.writerow(
                            [
                                photo["id"],
                                photo["sample_id"],
                                photo["batch_name"],
                                photo["created_at"],
                                photo["tags"],
                                photo["note"],
                                json.loads(photo["metadata"]).get("custom", {}),
                                "" if crops_only else manifest["files"][photo["original"]]["path"],
                                ";".join(
                                    manifest["files"][c["path"]]["path"] for p in history for c in p["crops"]
                                ),
                            ]
                        )
                        if progress:
                            progress(index + 1, total)
                    for index, video in enumerate(videos):
                        path = video["original"]
                        with (
                            self.path(path).open("rb") as src,
                            archive.open(path, "w", force_zip64=True) as dst,
                        ):
                            sha = copy_hashed(src, dst, cancel)
                        manifest["files"][path] = dict(path=path, sha256=sha)
                        if progress:
                            progress(len(photos) + index + 1, total)
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    archive.writestr("metadata.csv", "\ufeff" + csv_file.getvalue())
                    archive.writestr("templates.json", dump(manifest["templates"]))
                if cancel.is_set():
                    raise Cancelled("已取消导出")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return total

    def import_zip(self, source, *, cancel=None, progress=None):
        cancel = cancel or threading.Event()
        transaction_dir = self.root / ".staging" / uid()
        transaction_dir.mkdir()
        moved = []
        try:
            with self._write_lock, zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                names = [i.filename for i in infos]
                if len(names) != len(set(names)):
                    raise ValueError("压缩包含重复文件路径")
                for name in names:
                    p = PurePosixPath(name)
                    if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
                        raise ValueError("压缩包包含不安全路径")
                size = sum(i.file_size for i in infos)
                if size + 100 * 1024 * 1024 > shutil.disk_usage(self.root).free:
                    raise OSError("磁盘空间不足以恢复压缩包")
                if archive.getinfo("manifest.json").file_size > 100 * 1024 * 1024:
                    raise ValueError("清单文件过大")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("format") != "fieldwork" or manifest.get("version") not in (1, 2):
                    raise ValueError("不支持的归档格式或版本")
                if not manifest.get("complete"):
                    raise ValueError("此压缩包仅包含裁剪图，不能完整恢复工作区")
                dataset_id = uid()
                batch_ids = {b["id"]: uid() for b in manifest["batches"]}
                if len(batch_ids) != len(manifest["batches"]):
                    raise ValueError("批次编号重复")
                records = []
                video_records = []
                video_ids = set()
                total = len(manifest["photos"]) + len(manifest.get("videos", []))
                photo_ids = set()
                for index, photo in enumerate(manifest["photos"]):
                    if cancel.is_set():
                        raise Cancelled("已取消导入")
                    if photo["id"] in photo_ids:
                        raise ValueError("图片编号重复")
                    photo_ids.add(photo["id"])
                    photo_id = uid()
                    folder = transaction_dir / photo_id
                    folder.mkdir()
                    history = json.loads(photo["processing"])
                    paths = [photo["original"]] + [c["path"] for p in history for c in p["crops"]]
                    mapping = {}
                    for old in paths:
                        parts = PurePosixPath(old).parts
                        if len(parts) < 3 or parts[0] != "images" or parts[1] != photo["id"] or ".." in parts:
                            raise ValueError("图片关联路径不正确")
                        entry = manifest["files"][old]
                        data = archive.read(entry["path"])
                        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                            raise ValueError("文件校验失败：" + entry["path"])
                        with Image.open(io.BytesIO(data)) as image:
                            image.verify()
                        target = folder.joinpath(*parts[2:])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        mapping[old] = f"images/{photo_id}/" + "/".join(parts[2:])
                    for p in history:
                        for c in p["crops"]:
                            c["path"] = mapping[c["path"]]
                    records.append(
                        (
                            photo_id,
                            dataset_id,
                            batch_ids[photo["batch_id"]],
                            photo["created_at"],
                            photo["sample_id"],
                            photo["tags"],
                            photo["note"],
                            photo["metadata"],
                            photo["width"],
                            photo["height"],
                            mapping[photo["original"]],
                            dump(history),
                        )
                    )
                    if progress:
                        progress(index + 1, total)
                for index, video in enumerate(manifest.get("videos", [])):
                    old_id = video["id"]
                    if old_id in video_ids or video["original"] != f"videos/{old_id}.mp4":
                        raise ValueError("视频编号重复或关联路径不正确")
                    video_ids.add(old_id)
                    video_id = uid()
                    folder = transaction_dir / "videos"
                    folder.mkdir(exist_ok=True)
                    entry = manifest["files"][video["original"]]
                    with archive.open(entry["path"]) as src, (folder / f"{video_id}.mp4").open("wb") as dst:
                        sha = copy_hashed(src, dst, cancel)
                    if sha != entry["sha256"]:
                        raise ValueError("视频校验失败：" + entry["path"])
                    video_records.append(
                        (
                            video_id,
                            dataset_id,
                            batch_ids[video["batch_id"]],
                            video["created_at"],
                            video["sample_id"],
                            video["tags"],
                            video["note"],
                            video["metadata"],
                            video["duration_ms"],
                            f"videos/{video_id}.mp4",
                        )
                    )
                    if progress:
                        progress(len(manifest["photos"]) + index + 1, total)
                if cancel.is_set():
                    raise Cancelled("已取消导入")
                with self.connect() as db:
                    ds = manifest["dataset"]
                    db.execute(
                        "INSERT INTO datasets(id,name,description,tags,created_at,archived) VALUES(?,?,?,?,?,?)",
                        (
                            dataset_id,
                            check_name(ds["name"]),
                            ds["description"],
                            ds["tags"],
                            ds["created_at"],
                            ds["archived"],
                        ),
                    )
                    for b in manifest["batches"]:
                        db.execute(
                            "INSERT INTO batches(id,dataset_id,name,defaults,created_at) VALUES(?,?,?,?,?)",
                            (batch_ids[b["id"]], dataset_id, b["name"], b["defaults"], b["created_at"]),
                        )
                    for t in manifest["templates"]:
                        db.execute(
                            "INSERT INTO templates VALUES(?,?,?,?,?,?,?,?)",
                            (
                                uid(),
                                dataset_id,
                                t["name"],
                                t["version"],
                                t["width"],
                                t["height"],
                                dump(t["regions"]),
                                t["created_at"],
                            ),
                        )
                    for record in records:
                        final = self.root / "images" / record[0]
                        os.replace(transaction_dir / record[0], final)
                        moved.append(final)
                    db.executemany(
                        """INSERT INTO photos(id,dataset_id,batch_id,created_at,sample_id,tags,note,
                        metadata,width,height,original,processing) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        records,
                    )
                    for record in video_records:
                        final = self.path(record[-1])
                        os.replace(transaction_dir / "videos" / final.name, final)
                        moved.append(final)
                    db.executemany(
                        """INSERT INTO videos(id,dataset_id,batch_id,created_at,sample_id,tags,note,
                        metadata,duration_ms,original) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        video_records,
                    )
            return dataset_id
        except BaseException:
            for folder in moved:
                if folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)
                else:
                    folder.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(transaction_dir, ignore_errors=True)

    def backup(self, destination, *, cancel=None, progress=None):
        cancel = cancel or threading.Event()
        temporary = Path(destination).with_name(Path(destination).name + "." + uid() + ".partial")
        staging = self.root / ".staging" / uid()
        staging.mkdir()
        try:
            with self._write_lock:
                backup_db = staging / "archive.sqlite3"
                with self.connect() as db, closing(sqlite3.connect(backup_db)) as target:
                    db.backup(target)
                files = [
                    backup_db,
                    *(self.root / "images").rglob("*.png"),
                    *(self.root / "videos").glob("*.mp4"),
                ]
                manifest = dict(format="fieldwork-workspace", version=1, files={})
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                    for i, path in enumerate(files):
                        if cancel.is_set():
                            raise Cancelled("已取消工作区备份")
                        name = (
                            "archive.sqlite3" if path == backup_db else path.relative_to(self.root).as_posix()
                        )
                        with path.open("rb") as src, archive.open(name, "w", force_zip64=True) as dst:
                            manifest["files"][name] = copy_hashed(src, dst, cancel)
                        if progress:
                            progress(i + 1, len(files))
                    archive.writestr("backup.json", dump(manifest))
                if cancel.is_set():
                    raise Cancelled("已取消工作区备份")
                os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def restore_backup(source, destination, *, cancel=None, progress=None):
        cancel = cancel or threading.Event()
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError("恢复目标已存在，请选择新位置")
        staging = destination.with_name(".fieldwork-restore-" + uid())
        staging.mkdir(parents=True)
        try:
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if len({i.filename for i in infos}) != len(infos):
                    raise ValueError("备份包含重复文件路径")
                if sum(i.file_size for i in infos) + 100 * 1024 * 1024 > shutil.disk_usage(staging).free:
                    raise OSError("恢复位置剩余空间不足")
                if archive.getinfo("backup.json").file_size > 100 * 1024 * 1024:
                    raise ValueError("备份清单过大")
                manifest = json.loads(archive.read("backup.json"))
                if manifest.get("format") != "fieldwork-workspace" or manifest.get("version") != 1:
                    raise ValueError("请选择完整工作区备份；数据集归档请在首页导入")
                for i, (name, sha) in enumerate(manifest["files"].items()):
                    if cancel.is_set():
                        raise Cancelled("已取消恢复")
                    p = PurePosixPath(name)
                    if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
                        raise ValueError("备份包含不安全路径")
                    if name != "archive.sqlite3" and not (
                        (len(p.parts) >= 3 and p.parts[0] == "images" and p.suffix == ".png")
                        or (len(p.parts) == 2 and p.parts[0] == "videos" and p.suffix == ".mp4")
                    ):
                        raise ValueError("备份包含未知文件")
                    target = staging.joinpath(*p.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as src, target.open("wb") as dst:
                        actual_sha = copy_hashed(src, dst, cancel)
                    if actual_sha != sha:
                        raise ValueError("备份文件校验失败：" + name)
                    if progress:
                        progress(i + 1, len(manifest["files"]))
            if not (staging / "archive.sqlite3").is_file():
                raise ValueError("备份缺少数据库")
            with closing(sqlite3.connect(staging / "archive.sqlite3")) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("备份数据库损坏")
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("备份数据库关联异常")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (1, SCHEMA_VERSION):
                    raise ValueError("不支持此数据库版本")
                for original, processing in db.execute("SELECT original,processing FROM photos"):
                    for name in [original, *(c["path"] for p in json.loads(processing) for c in p["crops"])]:
                        target = (staging / name).resolve()
                        if not target.is_relative_to(staging) or not target.is_file():
                            raise ValueError("备份缺少关联图像")
                if version >= 2:
                    for (name,) in db.execute("SELECT original FROM videos"):
                        target = (staging / name).resolve()
                        if not target.is_relative_to(staging) or not target.is_file():
                            raise ValueError("备份缺少关联视频")
            if cancel.is_set():
                raise Cancelled("已取消恢复")
            os.replace(staging, destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
