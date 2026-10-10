"""Tests for state management module."""

import time
import pytest
from app.state import (
    CLOUDINARY_UPLOADED, FAILED, PENDING, PROCESSED, PROCESSING,
    PUBLISHED, READY_TO_PUBLISH, RETRY_PENDING,
    INSTAGRAM_PROCESSING, StateDB,
)


class TestSourceOperations:
    def test_upsert_and_get_source(self, db):
        src = db.upsert_source("file_abc", filename="test.mp4", expected_size=1000)
        assert src.drive_file_id == "file_abc"
        assert src.filename == "test.mp4"
        assert src.download_status == "pending"

    def test_upsert_updates_existing(self, db):
        db.upsert_source("file_abc", filename="test.mp4")
        updated = db.upsert_source("file_abc", download_status="complete", actual_size=999)
        assert updated.download_status == "complete"
        assert updated.actual_size == 999
        assert updated.filename == "test.mp4"  # preserved

    def test_source_already_exists_valid(self, db):
        """Test case 1: Source already exists and is valid."""
        src = db.upsert_source(
            "file_123",
            filename="ep1.mp4",
            download_status="complete",
            local_path="/data/sources/ep1.mp4",
            actual_size=5000,
        )
        retrieved = db.get_source_by_drive_id("file_123")
        assert retrieved is not None
        assert retrieved.download_status == "complete"

    def test_source_missing(self, db):
        """Test case 2: Source is missing and requires downloading."""
        result = db.get_source_by_drive_id("nonexistent")
        assert result is None


class TestClipOperations:
    def test_create_clip(self, db):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        assert clip.clip_index == 0
        assert clip.state == PENDING

    def test_create_clip_idempotent(self, db):
        src = db.upsert_source("file_1")
        c1 = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        c2 = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        assert c1.id == c2.id

    def test_update_clip_state(self, db):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, PROCESSING)
        updated = db.get_clip(clip.id)
        assert updated.state == PROCESSING

    def test_invalid_transition(self, db):
        """Test case 17: Previously published clip is encountered again."""
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        # Walk to PUBLISHED
        db.update_clip_state(clip.id, PROCESSING)
        db.update_clip_state(clip.id, PROCESSED)
        db.update_clip_state(clip.id, CLOUDINARY_UPLOADED)
        db.update_clip_state(clip.id, INSTAGRAM_PROCESSING)
        db.update_clip_state(clip.id, READY_TO_PUBLISH)
        db.update_clip_state(clip.id, PUBLISHED)
        # Cannot go back
        with pytest.raises(ValueError, match="Invalid transition"):
            db.update_clip_state(clip.id, PENDING)

    def test_full_lifecycle(self, db):
        """Full state machine walkthrough."""
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)

        db.update_clip_state(clip.id, PROCESSING)
        db.update_clip_state(clip.id, PROCESSED, output_path="/clips/c.mp4", codec="h264")
        db.update_clip_state(clip.id, CLOUDINARY_UPLOADED,
                            cloudinary_id="pub_123", cloudinary_url="https://res.cloudinary.com/v.mp4")
        db.update_clip_state(clip.id, INSTAGRAM_PROCESSING,
                            instagram_container_id="ctr_456")
        db.update_clip_state(clip.id, READY_TO_PUBLISH)
        db.update_clip_state(clip.id, PUBLISHED, instagram_media_id="media_789")

        final = db.get_clip(clip.id)
        assert final.state == PUBLISHED
        assert final.instagram_media_id == "media_789"

    def test_failure_increments_attempt(self, db):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, PROCESSING)
        db.update_clip_state(clip.id, FAILED, last_error="FFmpeg crash")
        updated = db.get_clip(clip.id)
        assert updated.attempt_count == 1
        assert updated.last_error == "FFmpeg crash"

    def test_next_eligible_clip(self, db):
        src = db.upsert_source("file_1")
        db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.create_clip(src.id, 1, 60.0, 120.0, 60.0)
        clip = db.next_eligible_clip(src.id)
        assert clip is not None
        assert clip.clip_index == 0

    def test_list_clips_by_state(self, db):
        src = db.upsert_source("file_1")
        c0 = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        c1 = db.create_clip(src.id, 1, 60.0, 120.0, 60.0)
        db.update_clip_state(c0.id, PROCESSING)
        db.update_clip_state(c0.id, PROCESSED)

        pending = db.list_clips(src.id, state=PENDING)
        assert len(pending) == 1
        assert pending[0].clip_index == 1

        processed = db.list_clips(src.id, state=PROCESSED)
        assert len(processed) == 1
        assert processed[0].clip_index == 0


class TestRetryEligible:
    def test_retry_eligible(self, db):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.update_clip_state(clip.id, PROCESSING)
        db.update_clip_state(clip.id, FAILED, last_error="timeout")

        eligible = db.get_retry_eligible(src.id, max_retries=3)
        assert len(eligible) == 1

    def test_retry_max_exceeded(self, db):
        """Test case 18 (partial): Two workers — max retries exceeded."""
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)

        # Exhaust retries
        for _ in range(3):
            clip = db.get_clip(clip.id)
            if clip.state == FAILED:
                db.update_clip_state(clip.id, RETRY_PENDING)
                db.update_clip_state(clip.id, PENDING)
            db.update_clip_state(clip.id, PROCESSING)
            db.update_clip_state(clip.id, FAILED)

        eligible = db.get_retry_eligible(src.id, max_retries=3)
        assert len(eligible) == 0


class TestSchedulerState:
    def test_set_get_state(self, db):
        db.set_scheduler_state("last_run", "12345")
        assert db.get_scheduler_state("last_run") == "12345"

    def test_lock_acquire_release(self, db):
        assert db.try_acquire_lock("scheduler", "worker1") is True
        # Same worker can refresh
        db.refresh_lock("scheduler", "worker1")
        # Another worker cannot acquire
        assert db.try_acquire_lock("scheduler", "worker2") is False
        # Release and re-acquire
        db.release_lock("scheduler")
        assert db.try_acquire_lock("scheduler", "worker2") is True


class TestStatusSummary:
    def test_summary(self, db):
        src = db.upsert_source("file_1")
        db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        db.create_clip(src.id, 1, 60.0, 120.0, 60.0)
        summary = db.status_summary(src.id)
        assert summary["total_clips"] == 2
        assert summary["pending"] == 2


class TestProcessingAttempts:
    def test_record_and_complete(self, db):
        src = db.upsert_source("file_1")
        clip = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        aid = db.record_attempt(clip.id, "processing", 1)
        db.complete_attempt(aid, success=True)
        # No assertion needed — just verifies no crash


class TestRecovery:
    def test_worker_restart_with_pending(self, db):
        """Test case 16: Worker restarts with pending jobs."""
        src = db.upsert_source("file_1", download_status="complete")
        c0 = db.create_clip(src.id, 0, 0.0, 60.0, 60.0)
        c1 = db.create_clip(src.id, 1, 60.0, 120.0, 60.0)

        # Simulate partial progress
        db.update_clip_state(c0.id, PROCESSING)
        db.update_clip_state(c0.id, PROCESSED)
        db.update_clip_state(c0.id, CLOUDINARY_UPLOADED)

        # After restart, next eligible should be c0 (CLOUDINARY_UPLOADED)
        # then c1 (PENDING)
        next_clip = db.next_eligible_clip(src.id)
        assert next_clip.id == c0.id
        assert next_clip.state == CLOUDINARY_UPLOADED
