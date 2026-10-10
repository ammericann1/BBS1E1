"""
Instagram Graph API integration for Reel publishing.

Implements container creation, status polling, and publication
with safety checks, duplicate prevention, and error handling.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

from app.config import Config
from app.logging_config import get_logger
from app.state import (
    CLOUDINARY_UPLOADED, FAILED, INSTAGRAM_PROCESSING,
    PUBLISHED, READY_TO_PUBLISH, StateDB, ClipRecord,
)

log = get_logger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com"
POLL_INTERVAL = 10  # seconds between status checks
POLL_TIMEOUT = 300  # max seconds to wait for container readiness
REQUEST_TIMEOUT = 30  # HTTP request timeout


class InstagramError(Exception):
    """Raised on Instagram API failures."""


class InstagramPermanentError(InstagramError):
    """Non-retryable Instagram error."""


class InstagramTransientError(InstagramError):
    """Retryable Instagram error."""


def _api_url(config: Config, path: str) -> str:
    """Build a Graph API URL."""
    return f"{GRAPH_API_BASE}/{config.instagram_api_version}/{path}"


def _handle_error(response: requests.Response, context: str) -> None:
    """Parse and raise appropriate errors from API responses."""
    try:
        data = response.json()
    except ValueError:
        raise InstagramTransientError(
            f"{context}: HTTP {response.status_code} (non-JSON response)"
        )

    error = data.get("error", {})
    code = error.get("code", 0)
    msg = error.get("message", "Unknown error")
    error_subcode = error.get("error_subcode", 0)

    # Classify errors
    # Rate limiting
    if code == 4 or code == 32 or response.status_code == 429:
        raise InstagramTransientError(f"{context}: Rate limited — {msg}")

    # Token/permission errors
    if code in (190, 10, 200, 100):
        raise InstagramPermanentError(f"{context}: {msg} (code={code})")

    # Transient server errors
    if response.status_code >= 500:
        raise InstagramTransientError(f"{context}: Server error — {msg}")

    raise InstagramError(f"{context}: {msg} (code={code}, subcode={error_subcode})")


def validate_account(config: Config) -> dict[str, Any]:
    """
    Validate the Instagram account configuration.

    Checks that the access token and user ID are valid and the
    account has the required permissions.
    """
    ig_log = log.with_fields(user_id=config.instagram_user_id)

    # Check token validity and user info
    url = _api_url(config, config.instagram_user_id)
    try:
        resp = requests.get(
            url,
            params={
                "fields": "id,username,account_type,media_count",
                "access_token": config.instagram_access_token,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise InstagramTransientError(f"Failed to reach Instagram API: {e}")

    if resp.status_code != 200:
        _handle_error(resp, "Account validation")

    data = resp.json()
    ig_log.info("Account validated", extra={"_structured": {
        "username": data.get("username"),
        "account_type": data.get("account_type"),
        "media_count": data.get("media_count"),
    }})
    return data


def create_reel_container(
    config: Config,
    video_url: str,
    caption: str = "",
) -> str:
    """
    Create an Instagram Reel media container.

    Returns the container ID. Does NOT publish the Reel.
    """
    url = _api_url(config, f"{config.instagram_user_id}/media")

    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": config.instagram_access_token,
    }

    try:
        resp = requests.post(url, data=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise InstagramTransientError(f"Container creation request failed: {e}")

    if resp.status_code != 200:
        _handle_error(resp, "Container creation")

    data = resp.json()
    container_id = data.get("id")
    if not container_id:
        raise InstagramError("Container creation returned no ID")

    log.info("Reel container created", extra={"_structured": {
        "container_id": container_id,
    }})
    return container_id


def check_container_status(
    config: Config,
    container_id: str,
) -> dict[str, Any]:
    """
    Check the processing status of a media container.

    Returns dict with 'status_code' and optional 'error_message'.
    """
    url = _api_url(config, container_id)

    try:
        resp = requests.get(
            url,
            params={
                "fields": "status_code,status",
                "access_token": config.instagram_access_token,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise InstagramTransientError(f"Status check request failed: {e}")

    if resp.status_code != 200:
        _handle_error(resp, "Container status check")

    return resp.json()


def wait_for_container_ready(
    config: Config,
    container_id: str,
    *,
    timeout: int = POLL_TIMEOUT,
    poll_interval: int = POLL_INTERVAL,
) -> str:
    """
    Poll a container until it is ready for publishing or fails.

    Returns the final status code ('FINISHED' when ready).
    """
    ig_log = log.with_fields(container_id=container_id)
    start = time.time()
    last_status = "IN_PROGRESS"

    while time.time() - start < timeout:
        status_data = check_container_status(config, container_id)
        status_code = status_data.get("status_code", "UNKNOWN")
        last_status = status_code

        ig_log.info(f"Container status: {status_code}")

        if status_code == "FINISHED":
            return status_code

        if status_code == "ERROR":
            error_msg = status_data.get("status", "Unknown processing error")
            raise InstagramPermanentError(
                f"Container processing failed: {error_msg}"
            )

        if status_code not in ("IN_PROGRESS", "PUBLISHED"):
            ig_log.warning(f"Unexpected container status: {status_code}")

        time.sleep(poll_interval)

    raise InstagramTransientError(
        f"Container not ready after {timeout}s (last status: {last_status})"
    )


def publish_container(
    config: Config,
    container_id: str,
) -> str:
    """
    Publish a ready media container.

    Returns the published media ID.
    """
    url = _api_url(config, f"{config.instagram_user_id}/media_publish")

    try:
        resp = requests.post(
            url,
            data={
                "creation_id": container_id,
                "access_token": config.instagram_access_token,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise InstagramTransientError(f"Publish request failed: {e}")

    if resp.status_code != 200:
        _handle_error(resp, "Publish")

    data = resp.json()
    media_id = data.get("id")
    if not media_id:
        raise InstagramError("Publish returned no media ID")

    log.info("Reel published", extra={"_structured": {
        "container_id": container_id,
        "media_id": media_id,
    }})
    return media_id


def get_media_status(
    config: Config,
    media_id: str,
) -> dict[str, Any]:
    """Retrieve the status of a published media item."""
    url = _api_url(config, media_id)

    try:
        resp = requests.get(
            url,
            params={
                "fields": "id,media_type,timestamp,permalink",
                "access_token": config.instagram_access_token,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise InstagramTransientError(f"Media status request failed: {e}")

    if resp.status_code != 200:
        _handle_error(resp, "Media status")

    return resp.json()


def publish_clip(
    clip: ClipRecord,
    config: Config,
    db: StateDB,
    *,
    dry_run: bool = False,
    auto_publish: bool = False,
) -> Optional[str]:
    """
    Full publication flow for a single clip.

    1. Create container
    2. Wait for processing
    3. Publish (if auto_publish and not dry_run)

    Returns the media ID on success, or None.
    """
    if not clip.cloudinary_url:
        raise InstagramError(f"Clip {clip.id} has no Cloudinary URL")

    ig_log = log.with_fields(clip_id=clip.id)

    if dry_run:
        ig_log.info("DRY RUN: Would publish clip to Instagram")
        return None

    # Create container
    caption = f"Episode clip {clip.clip_index + 1}"
    attempt_id = db.record_attempt(clip.id, "instagram_publish", clip.attempt_count + 1)

    try:
        container_id = create_reel_container(
            config, clip.cloudinary_url, caption=caption
        )
        db.update_clip_state(
            clip.id, INSTAGRAM_PROCESSING,
            instagram_container_id=container_id,
        )
        db.upsert_instagram_job(clip.id, container_id=container_id, status="processing")

        # Wait for container readiness
        status = wait_for_container_ready(config, container_id)

        db.update_clip_state(clip.id, READY_TO_PUBLISH)
        db.upsert_instagram_job(clip.id, status="ready")

        if not auto_publish:
            ig_log.info("Container ready. AUTO_PUBLISH is off — skipping publish.")
            db.complete_attempt(attempt_id, success=True)
            return None

        # Publish
        media_id = publish_container(config, container_id)
        db.update_clip_state(
            clip.id, PUBLISHED,
            instagram_media_id=media_id,
        )
        db.upsert_instagram_job(clip.id, media_id=media_id, status="published")
        db.complete_attempt(attempt_id, success=True)

        ig_log.info("Clip published to Instagram", extra={"_structured": {
            "media_id": media_id,
        }})
        return media_id

    except InstagramPermanentError as e:
        db.update_clip_state(clip.id, FAILED, last_error=str(e))
        db.upsert_instagram_job(
            clip.id, status="failed",
            error_code="permanent", error_message=str(e),
        )
        db.complete_attempt(attempt_id, success=False, error_message=str(e))
        raise

    except (InstagramTransientError, InstagramError) as e:
        db.update_clip_state(clip.id, FAILED, last_error=str(e))
        db.upsert_instagram_job(
            clip.id, status="failed",
            error_code="transient", error_message=str(e),
        )
        db.complete_attempt(attempt_id, success=False, error_message=str(e))
        raise
