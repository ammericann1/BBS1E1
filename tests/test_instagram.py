"""Tests for Instagram Graph API integration with mocked HTTP."""

import pytest
from unittest.mock import patch, MagicMock

from app.instagram_client import (
    InstagramError,
    InstagramPermanentError,
    InstagramTransientError,
    validate_account,
    create_reel_container,
    check_container_status,
    wait_for_container_ready,
    publish_container,
    publish_clip,
)
from app.state import (
    CLOUDINARY_UPLOADED, FAILED, INSTAGRAM_PROCESSING,
    PUBLISHED, READY_TO_PUBLISH,
)


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


class TestValidateAccount:
    @patch("app.instagram_client.requests.get")
    def test_valid_account(self, mock_get, config):
        mock_get.return_value = _mock_response(200, {
            "id": "17841400000000000",
            "username": "testuser",
            "account_type": "BUSINESS",
            "media_count": 42,
        })
        data = validate_account(config)
        assert data["username"] == "testuser"

    @patch("app.instagram_client.requests.get")
    def test_invalid_token(self, mock_get, config):
        mock_get.return_value = _mock_response(400, {
            "error": {"code": 190, "message": "Invalid token"}
        })
        with pytest.raises(InstagramPermanentError, match="Invalid token"):
            validate_account(config)


class TestContainerCreation:
    @patch("app.instagram_client.requests.post")
    def test_create_success(self, mock_post, config):
        mock_post.return_value = _mock_response(200, {"id": "container_123"})
        cid = create_reel_container(config, "https://res.cloudinary.com/v.mp4")
        assert cid == "container_123"

    @patch("app.instagram_client.requests.post")
    def test_create_rate_limited(self, mock_post, config):
        mock_post.return_value = _mock_response(429, {
            "error": {"code": 4, "message": "Rate limited"}
        })
        with pytest.raises(InstagramTransientError, match="Rate limited"):
            create_reel_container(config, "https://example.com/v.mp4")


class TestContainerStatus:
    @patch("app.instagram_client.requests.get")
    def test_finished(self, mock_get, config):
        mock_get.return_value = _mock_response(200, {"status_code": "FINISHED"})
        status = check_container_status(config, "container_123")
        assert status["status_code"] == "FINISHED"

    @patch("app.instagram_client.requests.get")
    def test_status_error(self, mock_get, config):
        """Test case 13: Instagram reports a permanent media error."""
        mock_get.return_value = _mock_response(200, {
            "status_code": "ERROR",
            "status": "Video format not supported",
        })
        status = check_container_status(config, "container_123")
        assert status["status_code"] == "ERROR"


class TestWaitForReady:
    @patch("app.instagram_client.time.sleep")
    @patch("app.instagram_client.check_container_status")
    def test_wait_success(self, mock_status, mock_sleep, config):
        """Test case 12: Instagram media processing remains pending then succeeds."""
        mock_status.side_effect = [
            {"status_code": "IN_PROGRESS"},
            {"status_code": "IN_PROGRESS"},
            {"status_code": "FINISHED"},
        ]
        result = wait_for_container_ready(config, "container_123")
        assert result == "FINISHED"

    @patch("app.instagram_client.time.sleep")
    @patch("app.instagram_client.check_container_status")
    def test_wait_error(self, mock_status, mock_sleep, config):
        mock_status.return_value = {"status_code": "ERROR", "status": "Bad video"}
        with pytest.raises(InstagramPermanentError, match="processing failed"):
            wait_for_container_ready(config, "container_123")

    @patch("app.instagram_client.time.time")
    @patch("app.instagram_client.time.sleep")
    @patch("app.instagram_client.check_container_status")
    def test_wait_timeout(self, mock_status, mock_sleep, mock_time, config):
        """Test case 15: Publish request times out ambiguously."""
        mock_time.side_effect = [0, 100, 200, 400]  # Exceeds 300s timeout
        mock_status.return_value = {"status_code": "IN_PROGRESS"}
        with pytest.raises(InstagramTransientError, match="not ready"):
            wait_for_container_ready(config, "container_123")


class TestPublish:
    @patch("app.instagram_client.requests.post")
    def test_publish_success(self, mock_post, config):
        """Test case 14: Instagram publication succeeds."""
        mock_post.return_value = _mock_response(200, {"id": "media_789"})
        media_id = publish_container(config, "container_123")
        assert media_id == "media_789"

    @patch("app.instagram_client.requests.post")
    def test_publish_permission_error(self, mock_post, config):
        mock_post.return_value = _mock_response(400, {
            "error": {"code": 10, "message": "Permission denied"}
        })
        with pytest.raises(InstagramPermanentError):
            publish_container(config, "container_123")


class TestPublishClip:
    def _make_uploaded_clip(self, db):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, "PROCESSING")
        db.update_clip_state(clip.id, "PROCESSED", output_path="/clip.mp4")
        db.update_clip_state(
            clip.id, CLOUDINARY_UPLOADED,
            cloudinary_url="https://res.cloudinary.com/test/v.mp4",
        )
        return db.get_clip(clip.id)

    def test_dry_run_no_publish(self, config, db):
        """Test case 20: Dry-run mode does not publish any Reel."""
        clip = self._make_uploaded_clip(db)
        result = publish_clip(clip, config, db, dry_run=True)
        assert result is None
        # State should not change to PUBLISHED
        updated = db.get_clip(clip.id)
        assert updated.state == CLOUDINARY_UPLOADED

    @patch("app.instagram_client.wait_for_container_ready")
    @patch("app.instagram_client.create_reel_container")
    def test_auto_publish_off(self, mock_create, mock_wait, config, db):
        """Container created but not published when AUTO_PUBLISH=false."""
        clip = self._make_uploaded_clip(db)
        mock_create.return_value = "container_abc"
        mock_wait.return_value = "FINISHED"

        result = publish_clip(clip, config, db, auto_publish=False)
        assert result is None

        updated = db.get_clip(clip.id)
        assert updated.state == READY_TO_PUBLISH

    @patch("app.instagram_client.publish_container")
    @patch("app.instagram_client.wait_for_container_ready")
    @patch("app.instagram_client.create_reel_container")
    def test_full_publish_flow(self, mock_create, mock_wait, mock_publish, config, db):
        clip = self._make_uploaded_clip(db)
        mock_create.return_value = "container_abc"
        mock_wait.return_value = "FINISHED"
        mock_publish.return_value = "media_final_789"

        result = publish_clip(clip, config, db, auto_publish=True)
        assert result == "media_final_789"

        updated = db.get_clip(clip.id)
        assert updated.state == PUBLISHED
        assert updated.instagram_media_id == "media_final_789"

    @patch("app.instagram_client.create_reel_container")
    def test_permanent_failure(self, mock_create, config, db):
        clip = self._make_uploaded_clip(db)
        mock_create.side_effect = InstagramPermanentError("Invalid media")

        with pytest.raises(InstagramPermanentError):
            publish_clip(clip, config, db, auto_publish=True)

        updated = db.get_clip(clip.id)
        assert updated.state == FAILED
