"""Tests for scheduler module."""

import time
import pytest
from unittest.mock import patch, MagicMock

from app.scheduler import Scheduler
from app.state import (
    CLOUDINARY_UPLOADED, FAILED, PENDING, PROCESSED,
    PROCESSING, PUBLISHED, READY_TO_PUBLISH, RETRY_PENDING,
)


class TestSchedulerLock:
    def test_single_instance(self, config, db):
        """Test case 18: Two workers attempt to acquire the same job."""
        s1 = Scheduler(config, db)
        s2 = Scheduler(config, db)

        assert s1._acquire_lock() is True
        assert s2._acquire_lock() is False

        s1._release_lock()
        assert s2._acquire_lock() is True
        s2._release_lock()

    def test_lock_refresh(self, config, db):
        s = Scheduler(config, db)
        assert s._acquire_lock() is True
        s.db.refresh_lock("scheduler", s.worker_id)
        # Should still hold it
        s2 = Scheduler(config, db)
        assert s2._acquire_lock() is False
        s._release_lock()


class TestPublishTiming:
    def test_can_publish_initial(self, config, db):
        s = Scheduler(config, db)
        assert s._can_publish() is True  # No prior publish

    def test_respects_interval(self, config, db):
        s = Scheduler(config, db)
        s._record_publish()
        assert s._can_publish() is False  # Just published

    @patch("time.time")
    def test_interval_elapsed(self, mock_time, config, db):
        s = Scheduler(config, db)
        # Record publish at t=0
        mock_time.return_value = 0
        s._record_publish()
        # Check at t=3601
        mock_time.return_value = 3601
        assert s._can_publish() is True


class TestShowQueue:
    def test_queue_status(self, config, db):
        src = db.upsert_source("file_1")
        db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.create_clip(src.id, 1, 60.0, 120.0, 60.0)

        s = Scheduler(config, db)
        queue = s.show_queue(src.id)
        assert queue["total_clips"] == 2
        assert queue["pending"] == 2
        assert "interval_seconds" in queue


class TestProcessNextClip:
    def test_process_uploaded_clip(self, config, db, tmp_path):
        """Processes a CLOUDINARY_UPLOADED clip through Instagram flow."""
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, PROCESSING)
        output = tmp_path / "clip.mp4"
        output.write_bytes(b"data" * 100)
        db.update_clip_state(clip.id, PROCESSED, output_path=str(output))
        db.update_clip_state(
            clip.id, CLOUDINARY_UPLOADED,
            cloudinary_url="https://res.cloudinary.com/v.mp4",
        )

        s = Scheduler(config, db)

        with patch("app.scheduler.publish_clip") as mock_pub:
            result = s._process_next_clip(src.id)
            assert result is True
            mock_pub.assert_called_once()


class TestRetryFailed:
    def test_retry_eligible_clips(self, config, db):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, PROCESSING)
        db.update_clip_state(clip.id, FAILED, last_error="timeout")

        # Manually set updated_at to past to bypass backoff
        db._conn.execute(
            "UPDATE clips SET updated_at=? WHERE id=?",
            (time.time() - 7200, clip.id),
        )
        db._conn.commit()

        s = Scheduler(config, db)
        retried = s._retry_failed(src.id)
        assert retried == 1

        updated = db.get_clip(clip.id)
        assert updated.state == PENDING


class TestGracefulShutdown:
    def test_shutdown_flag(self, config, db):
        s = Scheduler(config, db)
        assert s._should_stop() is False
        s._shutdown = True
        assert s._should_stop() is True
