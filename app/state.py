"""
Persistent state management using SQLite.

Tracks source episodes, generated clips, Cloudinary assets,
Instagram publication jobs, processing attempts, and scheduling state.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, Optional

from app.logging_config import get_logger

log = get_logger(__name__)

# ── Clip lifecycle states ─────────────────────────────────────────────
PENDING = "PENDING"
PROCESSING = "PROCESSING"
PROCESSED = "PROCESSED"
CLOUDINARY_UPLOADED = "CLOUDINARY_UPLOADED"
INSTAGRAM_PROCESSING = "INSTAGRAM_PROCESSING"
READY_TO_PUBLISH = "READY_TO_PUBLISH"
PUBLISHED = "PUBLISHED"
FAILED = "FAILED"
RETRY_PENDING = "RETRY_PENDING"

VALID_STATES = {
    PENDING, PROCESSING, PROCESSED, CLOUDINARY_UPLOADED,
    INSTAGRAM_PROCESSING, READY_TO_PUBLISH, PUBLISHED,
    FAILED, RETRY_PENDING,
}

# ── Allowed state transitions ────────────────────────────────────────
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    PENDING: {PROCESSING, FAILED},
    PROCESSING: {PROCESSED, FAILED},
    PROCESSED: {CLOUDINARY_UPLOADED, FAILED, RETRY_PENDING},
    CLOUDINARY_UPLOADED: {INSTAGRAM_PROCESSING, FAILED, RETRY_PENDING},
    INSTAGRAM_PROCESSING: {READY_TO_PUBLISH, FAILED, RETRY_PENDING},
    READY_TO_PUBLISH: {PUBLISHED, FAILED, RETRY_PENDING},
    PUBLISHED: set(),  # terminal
    FAILED: {RETRY_PENDING, PENDING},
    RETRY_PENDING: {PENDING, PROCESSING, PROCESSED, CLOUDINARY_UPLOADED, FAILED},
}

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    drive_file_id   TEXT NOT NULL,
    filename        TEXT,
    expected_size   INTEGER,
    actual_size     INTEGER,
    checksum        TEXT,
    download_status TEXT NOT NULL DEFAULT 'pending',
    local_path      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(id),
    clip_index      INTEGER NOT NULL,
    start_time      REAL NOT NULL,
    end_time        REAL NOT NULL,
    duration        REAL NOT NULL,
    audio_stream    TEXT,
    codec           TEXT,
    output_path     TEXT,
    state           TEXT NOT NULL DEFAULT 'PENDING',
    cloudinary_id   TEXT,
    cloudinary_url  TEXT,
    instagram_container_id TEXT,
    instagram_media_id     TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE(source_id, clip_index)
);

CREATE TABLE IF NOT EXISTS cloudinary_assets (
    id              TEXT PRIMARY KEY,
    clip_id         TEXT NOT NULL REFERENCES clips(id),
    asset_id        TEXT,
    public_id       TEXT,
    secure_url      TEXT,
    resource_type   TEXT,
    upload_status   TEXT NOT NULL DEFAULT 'pending',
    uploaded_at     REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS instagram_jobs (
    id              TEXT PRIMARY KEY,
    clip_id         TEXT NOT NULL REFERENCES clips(id),
    container_id    TEXT,
    media_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    error_code      TEXT,
    error_message   TEXT,
    published_at    REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_attempts (
    id              TEXT PRIMARY KEY,
    clip_id         TEXT NOT NULL REFERENCES clips(id),
    stage           TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    started_at      REAL NOT NULL,
    completed_at    REAL,
    success         INTEGER,
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS scheduler_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clips_state ON clips(state);
CREATE INDEX IF NOT EXISTS idx_clips_source ON clips(source_id);
CREATE INDEX IF NOT EXISTS idx_instagram_jobs_clip ON instagram_jobs(clip_id);
"""


@dataclass
class SourceRecord:
    id: str
    drive_file_id: str
    filename: Optional[str]
    expected_size: Optional[int]
    actual_size: Optional[int]
    checksum: Optional[str]
    download_status: str
    local_path: Optional[str]
    created_at: float
    updated_at: float


@dataclass
class ClipRecord:
    id: str
    source_id: str
    clip_index: int
    start_time: float
    end_time: float
    duration: float
    audio_stream: Optional[str]
    codec: Optional[str]
    output_path: Optional[str]
    state: str
    cloudinary_id: Optional[str]
    cloudinary_url: Optional[str]
    instagram_container_id: Optional[str]
    instagram_media_id: Optional[str]
    attempt_count: int
    last_error: Optional[str]
    created_at: float
    updated_at: float


class StateDB:
    """SQLite-backed state store with single-writer protection."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            timeout=30,
            isolation_level="DEFERRED",
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.transaction():
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for a database transaction."""
        cursor = self._conn.cursor()
        try:
            cursor.execute("BEGIN")
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    # ── Source operations ────────────────────────────────────────────

    def upsert_source(
        self,
        drive_file_id: str,
        *,
        filename: Optional[str] = None,
        expected_size: Optional[int] = None,
        actual_size: Optional[int] = None,
        checksum: Optional[str] = None,
        download_status: str = "pending",
        local_path: Optional[str] = None,
    ) -> SourceRecord:
        now = time.time()
        existing = self.get_source_by_drive_id(drive_file_id)
        if existing:
            self._conn.execute(
                """UPDATE sources
                   SET filename=COALESCE(?,filename),
                       expected_size=COALESCE(?,expected_size),
                       actual_size=COALESCE(?,actual_size),
                       checksum=COALESCE(?,checksum),
                       download_status=?,
                       local_path=COALESCE(?,local_path),
                       updated_at=?
                   WHERE drive_file_id=?""",
                (
                    filename, expected_size, actual_size, checksum,
                    download_status, local_path, now, drive_file_id,
                ),
            )
            self._conn.commit()
            return self.get_source_by_drive_id(drive_file_id)  # type: ignore[return-value]
        src_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO sources
               (id,drive_file_id,filename,expected_size,actual_size,
                checksum,download_status,local_path,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                src_id, drive_file_id, filename, expected_size,
                actual_size, checksum, download_status, local_path,
                now, now,
            ),
        )
        self._conn.commit()
        return self.get_source(src_id)  # type: ignore[return-value]

    def get_source(self, source_id: str) -> Optional[SourceRecord]:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE id=?", (source_id,)
        ).fetchone()
        return SourceRecord(**dict(row)) if row else None

    def get_source_by_drive_id(self, drive_file_id: str) -> Optional[SourceRecord]:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE drive_file_id=?", (drive_file_id,)
        ).fetchone()
        return SourceRecord(**dict(row)) if row else None

    # ── Clip operations ──────────────────────────────────────────────

    def create_clip(
        self,
        source_id: str,
        clip_index: int,
        start_time: float,
        end_time: float,
        duration: float,
        audio_stream: Optional[str] = None,
    ) -> ClipRecord:
        """Insert a clip row if it does not exist (idempotent on source_id+clip_index)."""
        existing = self.get_clip_by_index(source_id, clip_index)
        if existing:
            return existing
        now = time.time()
        clip_id = f"{source_id[:8]}-clip{clip_index:04d}"
        self._conn.execute(
            """INSERT INTO clips
               (id,source_id,clip_index,start_time,end_time,duration,
                audio_stream,state,attempt_count,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,0,?,?)""",
            (
                clip_id, source_id, clip_index, start_time,
                end_time, duration, audio_stream, PENDING, now, now,
            ),
        )
        self._conn.commit()
        return self.get_clip(clip_id)  # type: ignore[return-value]

    def get_clip(self, clip_id: str) -> Optional[ClipRecord]:
        row = self._conn.execute(
            "SELECT * FROM clips WHERE id=?", (clip_id,)
        ).fetchone()
        return ClipRecord(**dict(row)) if row else None

    def get_clip_by_index(self, source_id: str, clip_index: int) -> Optional[ClipRecord]:
        row = self._conn.execute(
            "SELECT * FROM clips WHERE source_id=? AND clip_index=?",
            (source_id, clip_index),
        ).fetchone()
        return ClipRecord(**dict(row)) if row else None

    def list_clips(
        self, source_id: str, *, state: Optional[str] = None
    ) -> list[ClipRecord]:
        if state:
            rows = self._conn.execute(
                "SELECT * FROM clips WHERE source_id=? AND state=? ORDER BY clip_index",
                (source_id, state),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM clips WHERE source_id=? ORDER BY clip_index",
                (source_id,),
            ).fetchall()
        return [ClipRecord(**dict(r)) for r in rows]

    def update_clip_state(
        self,
        clip_id: str,
        new_state: str,
        *,
        output_path: Optional[str] = None,
        codec: Optional[str] = None,
        cloudinary_id: Optional[str] = None,
        cloudinary_url: Optional[str] = None,
        instagram_container_id: Optional[str] = None,
        instagram_media_id: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        if new_state not in VALID_STATES:
            raise ValueError(f"Invalid state: {new_state}")
        clip = self.get_clip(clip_id)
        if not clip:
            raise ValueError(f"Clip not found: {clip_id}")
        if new_state not in ALLOWED_TRANSITIONS.get(clip.state, set()):
            raise ValueError(
                f"Invalid transition {clip.state} → {new_state} for clip {clip_id}"
            )
        now = time.time()
        sets = ["state=?", "updated_at=?"]
        params: list[Any] = [new_state, now]
        if output_path is not None:
            sets.append("output_path=?"); params.append(output_path)
        if codec is not None:
            sets.append("codec=?"); params.append(codec)
        if cloudinary_id is not None:
            sets.append("cloudinary_id=?"); params.append(cloudinary_id)
        if cloudinary_url is not None:
            sets.append("cloudinary_url=?"); params.append(cloudinary_url)
        if instagram_container_id is not None:
            sets.append("instagram_container_id=?"); params.append(instagram_container_id)
        if instagram_media_id is not None:
            sets.append("instagram_media_id=?"); params.append(instagram_media_id)
        if last_error is not None:
            sets.append("last_error=?"); params.append(last_error)
        if new_state == FAILED:
            sets.append("attempt_count=attempt_count+1")
        params.append(clip_id)
        sql = f"UPDATE clips SET {','.join(sets)} WHERE id=?"
        self._conn.execute(sql, params)
        self._conn.commit()
        log.info(
            "Clip state updated",
            extra={"_structured": {
                "clip_id": clip_id,
                "from_state": clip.state,
                "to_state": new_state,
            }},
        )

    def next_eligible_clip(self, source_id: str) -> Optional[ClipRecord]:
        """Return the next clip ready for the next pipeline stage."""
        # Priority: READY_TO_PUBLISH > CLOUDINARY_UPLOADED > PROCESSED > PENDING
        for state in (READY_TO_PUBLISH, CLOUDINARY_UPLOADED, PROCESSED, PENDING):
            row = self._conn.execute(
                """SELECT * FROM clips
                   WHERE source_id=? AND state=?
                   ORDER BY clip_index ASC LIMIT 1""",
                (source_id, state),
            ).fetchone()
            if row:
                return ClipRecord(**dict(row))
        return None

    def next_publishable_clip(self, source_id: str) -> Optional[ClipRecord]:
        """Return the next clip that is ready to publish."""
        row = self._conn.execute(
            """SELECT * FROM clips
               WHERE source_id=? AND state=?
               ORDER BY clip_index ASC LIMIT 1""",
            (source_id, READY_TO_PUBLISH),
        ).fetchone()
        return ClipRecord(**dict(row)) if row else None

    def get_retry_eligible(self, source_id: str, max_retries: int) -> list[ClipRecord]:
        rows = self._conn.execute(
            """SELECT * FROM clips
               WHERE source_id=? AND state IN (?,?)
               AND attempt_count < ?
               ORDER BY clip_index ASC""",
            (source_id, FAILED, RETRY_PENDING, max_retries),
        ).fetchall()
        return [ClipRecord(**dict(r)) for r in rows]

    # ── Processing attempts ──────────────────────────────────────────

    def record_attempt(
        self,
        clip_id: str,
        stage: str,
        attempt_number: int,
        *,
        success: Optional[bool] = None,
        error_message: Optional[str] = None,
    ) -> str:
        now = time.time()
        attempt_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO processing_attempts
               (id,clip_id,stage,attempt_number,started_at,completed_at,success,error_message)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                attempt_id, clip_id, stage, attempt_number,
                now, now if success is not None else None,
                int(success) if success is not None else None,
                error_message,
            ),
        )
        self._conn.commit()
        return attempt_id

    def complete_attempt(
        self, attempt_id: str, *, success: bool, error_message: Optional[str] = None
    ) -> None:
        self._conn.execute(
            """UPDATE processing_attempts
               SET completed_at=?, success=?, error_message=?
               WHERE id=?""",
            (time.time(), int(success), error_message, attempt_id),
        )
        self._conn.commit()

    # ── Cloudinary asset tracking ────────────────────────────────────

    def upsert_cloudinary_asset(
        self,
        clip_id: str,
        *,
        asset_id: Optional[str] = None,
        public_id: Optional[str] = None,
        secure_url: Optional[str] = None,
        resource_type: str = "video",
        upload_status: str = "pending",
    ) -> str:
        now = time.time()
        existing = self._conn.execute(
            "SELECT id FROM cloudinary_assets WHERE clip_id=?", (clip_id,)
        ).fetchone()
        if existing:
            self._conn.execute(
                """UPDATE cloudinary_assets
                   SET asset_id=COALESCE(?,asset_id), public_id=COALESCE(?,public_id),
                       secure_url=COALESCE(?,secure_url), upload_status=?,
                       uploaded_at=?, updated_at=?
                   WHERE clip_id=?""",
                (asset_id, public_id, secure_url, upload_status, now, now, clip_id),
            )
            self._conn.commit()
            return existing["id"]
        rec_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO cloudinary_assets
               (id,clip_id,asset_id,public_id,secure_url,resource_type,
                upload_status,uploaded_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                rec_id, clip_id, asset_id, public_id, secure_url,
                resource_type, upload_status, now, now, now,
            ),
        )
        self._conn.commit()
        return rec_id

    # ── Instagram job tracking ───────────────────────────────────────

    def upsert_instagram_job(
        self,
        clip_id: str,
        *,
        container_id: Optional[str] = None,
        media_id: Optional[str] = None,
        status: str = "pending",
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> str:
        now = time.time()
        existing = self._conn.execute(
            "SELECT id FROM instagram_jobs WHERE clip_id=?", (clip_id,)
        ).fetchone()
        if existing:
            self._conn.execute(
                """UPDATE instagram_jobs
                   SET container_id=COALESCE(?,container_id),
                       media_id=COALESCE(?,media_id),
                       status=?, error_code=?, error_message=?,
                       published_at=CASE WHEN ?='published' THEN ? ELSE published_at END,
                       updated_at=?
                   WHERE clip_id=?""",
                (
                    container_id, media_id, status,
                    error_code, error_message,
                    status, now, now, clip_id,
                ),
            )
            self._conn.commit()
            return existing["id"]
        job_id = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO instagram_jobs
               (id,clip_id,container_id,media_id,status,error_code,
                error_message,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                job_id, clip_id, container_id, media_id, status,
                error_code, error_message, now, now,
            ),
        )
        self._conn.commit()
        return job_id

    # ── Scheduler state ──────────────────────────────────────────────

    def set_scheduler_state(self, key: str, value: str) -> None:
        now = time.time()
        self._conn.execute(
            """INSERT INTO scheduler_state (key,value,updated_at)
               VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=?,updated_at=?""",
            (key, value, now, value, now),
        )
        self._conn.commit()

    def get_scheduler_state(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM scheduler_state WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    # ── Status summary ───────────────────────────────────────────────

    def status_summary(self, source_id: str) -> dict[str, Any]:
        all_clips = self.list_clips(source_id)
        counts: dict[str, int] = {}
        for c in all_clips:
            counts[c.state] = counts.get(c.state, 0) + 1
        source = self.get_source(source_id)
        return {
            "source_id": source_id,
            "source_status": source.download_status if source else "unknown",
            "total_clips": len(all_clips),
            "clips_by_state": counts,
            "published": counts.get(PUBLISHED, 0),
            "failed": counts.get(FAILED, 0),
            "pending": counts.get(PENDING, 0),
        }

    # ── Acquire lock (single-worker) ─────────────────────────────────

    def try_acquire_lock(self, lock_name: str, worker_id: str) -> bool:
        """Attempt to acquire an advisory lock. Returns True if acquired."""
        now = time.time()
        existing = self.get_scheduler_state(f"lock:{lock_name}")
        if existing:
            # Check if the lock is stale (older than 5 minutes)
            row = self._conn.execute(
                "SELECT updated_at FROM scheduler_state WHERE key=?",
                (f"lock:{lock_name}",),
            ).fetchone()
            if row and (now - row["updated_at"]) < 300:
                return False  # Lock held by another worker
        self.set_scheduler_state(f"lock:{lock_name}", worker_id)
        return True

    def release_lock(self, lock_name: str) -> None:
        self._conn.execute(
            "DELETE FROM scheduler_state WHERE key=?", (f"lock:{lock_name}",)
        )
        self._conn.commit()

    def refresh_lock(self, lock_name: str, worker_id: str) -> None:
        self.set_scheduler_state(f"lock:{lock_name}", worker_id)
