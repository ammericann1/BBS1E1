"""
Google Drive source downloader.

Downloads large files incrementally, validates them, and caches
completed downloads for reuse across runs.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.config import Config
from app.logging_config import get_logger
from app.state import StateDB

log = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CHUNK_SIZE = 50 * 1024 * 1024  # 50 MB chunks
MIN_FREE_SPACE_GB = 6  # Minimum free disk space before starting downloads


class DownloadError(Exception):
    """Raised when a download fails."""


class InsufficientSpaceError(DownloadError):
    """Raised when there is not enough disk space."""


def _build_drive_service(config: Config):
    """Build an authenticated Google Drive API service."""
    creds_path = config.google_application_credentials
    if not creds_path:
        raise DownloadError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set. "
            "Provide a path to a service account JSON key file."
        )
    creds_file = Path(creds_path)
    if not creds_file.is_file():
        # Check if raw JSON is provided via env var
        raw_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if raw_json:
            import json
            import tempfile

            creds_data = json.loads(raw_json)
            fd, tmp_path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(creds_data, f)
            creds_file = Path(tmp_path)
        else:
            raise DownloadError(
                f"Credentials file not found: {creds_path}. "
                "Set GOOGLE_APPLICATION_CREDENTIALS to a valid service account key, "
                "or set GOOGLE_CREDENTIALS_JSON with the raw JSON content."
            )
    credentials = service_account.Credentials.from_service_account_file(
        str(creds_file), scopes=SCOPES
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _check_disk_space(path: Path, required_gb: float = MIN_FREE_SPACE_GB) -> None:
    """Verify sufficient disk space is available."""
    usage = shutil.disk_usage(str(path))
    free_gb = usage.free / (1024**3)
    if free_gb < required_gb:
        raise InsufficientSpaceError(
            f"Insufficient disk space: {free_gb:.1f} GB free, "
            f"need at least {required_gb:.1f} GB."
        )


def _compute_checksum(file_path: Path, algorithm: str = "md5") -> str:
    """Compute a file checksum incrementally."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192 * 1024)  # 8 MB
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _validate_download(file_path: Path, expected_size: Optional[int]) -> bool:
    """Check that a downloaded file is complete and non-empty."""
    if not file_path.is_file():
        return False
    actual_size = file_path.stat().st_size
    if actual_size == 0:
        return False
    if expected_size and actual_size != expected_size:
        log.warning(
            "Size mismatch",
            extra={"_structured": {
                "expected": expected_size,
                "actual": actual_size,
                "path": str(file_path),
            }},
        )
        return False
    return True


def download_source(config: Config, db: StateDB) -> Path:
    """
    Download the configured Google Drive file.

    Returns the path to the validated local source file.
    Reuses an existing valid download if available.
    """
    file_id = config.google_drive_file_id
    dl_log = log.with_fields(drive_file_id=file_id)

    # Check for existing valid source
    source = db.get_source_by_drive_id(file_id)
    if source and source.download_status == "complete" and source.local_path:
        local = Path(source.local_path)
        if local.is_file() and _validate_download(local, source.expected_size):
            dl_log.info("Reusing existing validated source", extra={"_structured": {
                "path": str(local),
                "size": local.stat().st_size,
            }})
            return local
        dl_log.warning("Previously-complete source is missing or invalid, re-downloading")

    # Build Drive service
    service = _build_drive_service(config)

    # Get file metadata
    try:
        meta = service.files().get(
            fileId=file_id, fields="id,name,size,mimeType"
        ).execute()
    except Exception as e:
        raise DownloadError(f"Failed to get file metadata: {e}")

    filename = meta.get("name", f"{file_id}.mp4")
    expected_size = int(meta["size"]) if "size" in meta else None
    dl_log = dl_log.with_fields(filename=filename, expected_size=expected_size)
    dl_log.info("Starting source download")

    # Ensure disk space
    config.sources_dir.mkdir(parents=True, exist_ok=True)
    if expected_size:
        needed_gb = (expected_size / (1024**3)) * 1.5  # 1.5x for safety
        _check_disk_space(config.sources_dir, max(needed_gb, MIN_FREE_SPACE_GB))
    else:
        _check_disk_space(config.sources_dir)

    # Record source in DB
    source = db.upsert_source(
        file_id,
        filename=filename,
        expected_size=expected_size,
        download_status="downloading",
    )

    final_path = config.sources_dir / filename
    temp_path = config.sources_dir / f".{filename}.downloading"

    # Download with streaming chunks
    try:
        request = service.files().get_media(fileId=file_id)
        with open(temp_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=CHUNK_SIZE)
            done = False
            last_log_time = time.time()
            while not done:
                status, done = downloader.next_chunk()
                if status and time.time() - last_log_time > 30:
                    dl_log.info(
                        f"Download progress: {status.progress() * 100:.1f}%",
                        extra={"_structured": {"progress": status.progress()}},
                    )
                    last_log_time = time.time()
    except Exception as e:
        db.upsert_source(file_id, download_status="failed")
        raise DownloadError(f"Download failed: {e}")

    # Validate
    if not _validate_download(temp_path, expected_size):
        db.upsert_source(file_id, download_status="failed")
        temp_path.unlink(missing_ok=True)
        raise DownloadError("Downloaded file failed validation (size mismatch or empty).")

    # Atomic rename
    temp_path.replace(final_path)
    actual_size = final_path.stat().st_size

    # Checksum (for files under 2 GB to avoid excessive time)
    checksum = None
    if actual_size < 2 * 1024**3:
        checksum = _compute_checksum(final_path)

    db.upsert_source(
        file_id,
        actual_size=actual_size,
        checksum=checksum,
        download_status="complete",
        local_path=str(final_path),
    )

    dl_log.info("Source download complete", extra={"_structured": {
        "path": str(final_path),
        "actual_size": actual_size,
        "checksum": checksum,
    }})
    return final_path
