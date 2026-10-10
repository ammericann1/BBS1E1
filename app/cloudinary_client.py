"""
Cloudinary integration for uploading processed video clips.

Uses the Cloudinary Python SDK with deterministic public IDs,
duplicate detection, and retry logic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import cloudinary
import cloudinary.uploader

from app.config import Config
from app.logging_config import get_logger
from app.state import (
    CLOUDINARY_UPLOADED, FAILED, PROCESSED, StateDB, ClipRecord,
)

log = get_logger(__name__)

UPLOAD_TIMEOUT = 600  # seconds
MAX_UPLOAD_RETRIES = 3


class CloudinaryError(Exception):
    """Raised on Cloudinary upload failures."""


def configure_cloudinary(config: Config) -> None:
    """Initialize the Cloudinary SDK with credentials."""
    cloudinary.config(
        cloud_name=config.cloudinary_cloud_name,
        api_key=config.cloudinary_api_key,
        api_secret=config.cloudinary_api_secret,
        secure=True,
    )


def _public_id_for_clip(clip: ClipRecord) -> str:
    """Generate a deterministic Cloudinary public ID for a clip."""
    return f"bbs_reels/{clip.source_id[:8]}/clip_{clip.clip_index:04d}"


def _check_existing_asset(public_id: str) -> Optional[dict[str, Any]]:
    """Check if an asset already exists in Cloudinary."""
    try:
        result = cloudinary.api.resource(
            public_id, resource_type="video"
        )
        if result and result.get("secure_url"):
            return result
    except cloudinary.exceptions.NotFound:
        return None
    except Exception as e:
        log.warning(f"Error checking existing Cloudinary asset: {e}")
    return None


def upload_clip(
    clip: ClipRecord,
    config: Config,
    db: StateDB,
    *,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Upload a processed clip to Cloudinary.

    Returns the secure HTTPS URL on success, or None on failure/dry-run.
    Skips upload if a valid asset already exists.
    """
    if not clip.output_path:
        raise CloudinaryError(f"Clip {clip.id} has no output path")

    clip_path = Path(clip.output_path)
    if not clip_path.is_file():
        raise CloudinaryError(f"Clip file not found: {clip_path}")

    public_id = _public_id_for_clip(clip)
    up_log = log.with_fields(clip_id=clip.id, public_id=public_id)

    if dry_run:
        up_log.info("DRY RUN: Would upload clip to Cloudinary")
        return None

    configure_cloudinary(config)

    # Check for existing asset (duplicate prevention)
    existing = _check_existing_asset(public_id)
    if existing:
        secure_url = existing["secure_url"]
        up_log.info("Cloudinary asset already exists, reusing", extra={"_structured": {
            "secure_url": secure_url,
        }})
        db.upsert_cloudinary_asset(
            clip.id,
            asset_id=existing.get("asset_id"),
            public_id=public_id,
            secure_url=secure_url,
            upload_status="complete",
        )
        db.update_clip_state(
            clip.id, CLOUDINARY_UPLOADED,
            cloudinary_id=public_id,
            cloudinary_url=secure_url,
        )
        return secure_url

    # Upload with retries
    last_error = None
    for attempt in range(1, MAX_UPLOAD_RETRIES + 1):
        attempt_id = db.record_attempt(clip.id, "cloudinary_upload", attempt)
        try:
            up_log.info(f"Uploading to Cloudinary (attempt {attempt})")
            result = cloudinary.uploader.upload(
                str(clip_path),
                resource_type="video",
                public_id=public_id,
                overwrite=False,  # Don't overwrite existing
                unique_filename=False,
                use_filename=False,
                timeout=UPLOAD_TIMEOUT,
            )

            secure_url = result.get("secure_url")
            asset_id = result.get("asset_id")

            if not secure_url or not secure_url.startswith("https://"):
                raise CloudinaryError(
                    f"Upload returned invalid URL: {secure_url}"
                )

            db.upsert_cloudinary_asset(
                clip.id,
                asset_id=asset_id,
                public_id=public_id,
                secure_url=secure_url,
                upload_status="complete",
            )
            db.update_clip_state(
                clip.id, CLOUDINARY_UPLOADED,
                cloudinary_id=public_id,
                cloudinary_url=secure_url,
            )
            db.complete_attempt(attempt_id, success=True)

            up_log.info("Upload complete", extra={"_structured": {
                "secure_url": secure_url,
                "asset_id": asset_id,
                "size": result.get("bytes"),
            }})
            return secure_url

        except CloudinaryError:
            raise
        except Exception as e:
            last_error = str(e)
            db.complete_attempt(attempt_id, success=False, error_message=last_error)
            up_log.warning(f"Upload attempt {attempt} failed: {e}")
            if attempt < MAX_UPLOAD_RETRIES:
                backoff = 2 ** attempt
                up_log.info(f"Retrying in {backoff}s")
                time.sleep(backoff)

    # All retries exhausted
    db.upsert_cloudinary_asset(clip.id, upload_status="failed")
    db.update_clip_state(clip.id, FAILED, last_error=f"Cloudinary upload failed: {last_error}")
    raise CloudinaryError(f"Upload failed after {MAX_UPLOAD_RETRIES} attempts: {last_error}")


def upload_eligible_clips(
    config: Config,
    db: StateDB,
    source_id: str,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Upload all PROCESSED clips to Cloudinary. Returns list of uploaded clip IDs."""
    clips = db.list_clips(source_id, state=PROCESSED)
    uploaded: list[str] = []

    for clip in clips:
        try:
            url = upload_clip(clip, config, db, dry_run=dry_run)
            if url:
                uploaded.append(clip.id)
        except CloudinaryError as e:
            log.error(f"Failed to upload clip {clip.id}: {e}")

    return uploaded
