"""Tests for Cloudinary integration with mocked SDK."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.cloudinary_client import (
    CloudinaryError,
    upload_clip,
    upload_eligible_clips,
)
from app.state import CLOUDINARY_UPLOADED, FAILED, PROCESSED


class TestCloudinaryUpload:
    def _make_clip(self, db, tmp_path, state=PROCESSED):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, "PROCESSING")
        output = tmp_path / "clip.mp4"
        output.write_bytes(b"fake video data" * 100)
        db.update_clip_state(clip.id, PROCESSED, output_path=str(output))
        return db.get_clip(clip.id)

    @patch("app.cloudinary_client._check_existing_asset", return_value=None)
    @patch("app.cloudinary_client.cloudinary.uploader.upload")
    @patch("app.cloudinary_client.configure_cloudinary")
    def test_upload_success(self, mock_cfg, mock_upload, mock_check, config, db, tmp_path):
        """Test successful Cloudinary upload."""
        clip = self._make_clip(db, tmp_path)

        mock_upload.return_value = {
            "secure_url": "https://res.cloudinary.com/test/video/upload/clip.mp4",
            "asset_id": "asset_abc123",
            "public_id": "bbs_reels/test/clip_0000",
            "bytes": 1500,
        }

        url = upload_clip(clip, config, db)
        assert url == "https://res.cloudinary.com/test/video/upload/clip.mp4"

        updated = db.get_clip(clip.id)
        assert updated.state == CLOUDINARY_UPLOADED
        assert updated.cloudinary_url is not None

    @patch("app.cloudinary_client._check_existing_asset")
    @patch("app.cloudinary_client.configure_cloudinary")
    def test_upload_duplicate_reuse(self, mock_cfg, mock_check, config, db, tmp_path):
        """Existing Cloudinary asset is reused without re-uploading."""
        clip = self._make_clip(db, tmp_path)

        mock_check.return_value = {
            "secure_url": "https://res.cloudinary.com/test/existing.mp4",
            "asset_id": "existing_asset",
        }

        url = upload_clip(clip, config, db)
        assert "existing.mp4" in url

        updated = db.get_clip(clip.id)
        assert updated.state == CLOUDINARY_UPLOADED

    @patch("app.cloudinary_client._check_existing_asset", return_value=None)
    @patch("app.cloudinary_client.cloudinary.uploader.upload")
    @patch("app.cloudinary_client.configure_cloudinary")
    def test_upload_failure_temporary(self, mock_cfg, mock_upload, mock_check, config, db, tmp_path):
        """Test case 10: Cloudinary upload fails temporarily then succeeds."""
        clip = self._make_clip(db, tmp_path)

        mock_upload.side_effect = [
            Exception("timeout"),
            Exception("timeout again"),
            {
                "secure_url": "https://res.cloudinary.com/test/clip.mp4",
                "asset_id": "asset_final",
                "bytes": 1500,
            },
        ]

        with patch("time.sleep"):
            url = upload_clip(clip, config, db)

        assert url is not None
        assert db.get_clip(clip.id).state == CLOUDINARY_UPLOADED

    @patch("app.cloudinary_client._check_existing_asset", return_value=None)
    @patch("app.cloudinary_client.cloudinary.uploader.upload")
    @patch("app.cloudinary_client.configure_cloudinary")
    def test_upload_all_retries_exhausted(self, mock_cfg, mock_upload, mock_check, config, db, tmp_path):
        """Upload fails after all retries."""
        clip = self._make_clip(db, tmp_path)
        mock_upload.side_effect = Exception("persistent failure")

        with patch("time.sleep"):
            with pytest.raises(CloudinaryError, match="failed after"):
                upload_clip(clip, config, db)

        assert db.get_clip(clip.id).state == FAILED

    def test_upload_dry_run(self, config, db, tmp_path):
        """Test case 20 (partial): Dry-run mode does not upload."""
        clip = self._make_clip(db, tmp_path)
        url = upload_clip(clip, config, db, dry_run=True)
        assert url is None
        assert db.get_clip(clip.id).state == PROCESSED  # Unchanged

    def test_upload_missing_file(self, config, db):
        """Upload with missing clip file raises error."""
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, "PROCESSING")
        db.update_clip_state(clip.id, PROCESSED, output_path="/nonexistent/clip.mp4")
        clip = db.get_clip(clip.id)

        with pytest.raises(CloudinaryError, match="not found"):
            upload_clip(clip, config, db)


class TestUploadEligible:
    @patch("app.cloudinary_client._check_existing_asset", return_value=None)
    @patch("app.cloudinary_client.cloudinary.uploader.upload")
    @patch("app.cloudinary_client.configure_cloudinary")
    def test_upload_multiple(self, mock_cfg, mock_upload, mock_check, config, db, tmp_path):
        src = db.upsert_source("file_1")

        for i in range(3):
            clip = db.create_clip(src.id, i, i * 60.0, (i + 1) * 60.0, 60.0)
            db.update_clip_state(clip.id, "PROCESSING")
            output = tmp_path / f"clip_{i}.mp4"
            output.write_bytes(b"data" * 100)
            db.update_clip_state(clip.id, PROCESSED, output_path=str(output))

        mock_upload.return_value = {
            "secure_url": "https://res.cloudinary.com/test/v.mp4",
            "asset_id": "a123",
            "bytes": 500,
        }

        uploaded = upload_eligible_clips(config, db, src.id)
        assert len(uploaded) == 3
