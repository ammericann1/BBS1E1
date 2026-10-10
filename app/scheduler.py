"""
Scheduler for automated clip processing and publication.

Runs as a single worker process, respects upload intervals,
implements exponential backoff, and supports graceful shutdown.
"""

from __future__ import annotations

import signal
import sys
import time
import uuid
from typing import Optional

from app.cloudinary_client import CloudinaryError, upload_clip
from app.config import Config
from app.downloader import DownloadError, download_source
from app.instagram_client import (
    InstagramError,
    InstagramPermanentError,
    publish_clip,
)
from app.logging_config import get_logger
from app.processor import ProcessingError, inspect_media, process_source
from app.state import (
    CLOUDINARY_UPLOADED,
    FAILED,
    PENDING,
    PROCESSED,
    PUBLISHED,
    READY_TO_PUBLISH,
    RETRY_PENDING,
    StateDB,
)

log = get_logger(__name__)

HEARTBEAT_INTERVAL = 60  # seconds


class Scheduler:
    """Single-worker scheduler with graceful shutdown and recovery."""

    def __init__(self, config: Config, db: StateDB) -> None:
        self.config = config
        self.db = db
        self.worker_id = str(uuid.uuid4())[:8]
        self._shutdown = False
        self._setup_signals()

    def _setup_signals(self) -> None:
        """Register signal handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        log.info(f"Received {sig_name}, shutting down gracefully")
        self._shutdown = True

    def _should_stop(self) -> bool:
        return self._shutdown

    def _acquire_lock(self) -> bool:
        """Acquire the scheduler lock to prevent concurrent execution."""
        if self.db.try_acquire_lock("scheduler", self.worker_id):
            log.info(f"Scheduler lock acquired (worker={self.worker_id})")
            return True
        log.warning("Another scheduler instance is running; exiting")
        return False

    def _release_lock(self) -> None:
        self.db.release_lock("scheduler")
        log.info("Scheduler lock released")

    def _last_publish_time(self) -> float:
        """Return the timestamp of the last publication."""
        raw = self.db.get_scheduler_state("last_publish_time")
        return float(raw) if raw else 0

    def _can_publish(self) -> bool:
        """Check if enough time has passed since the last publication."""
        elapsed = time.time() - self._last_publish_time()
        return elapsed >= self.config.upload_interval_seconds

    def _record_publish(self) -> None:
        self.db.set_scheduler_state("last_publish_time", str(time.time()))

    def _process_next_clip(self, source_id: str) -> bool:
        """
        Advance one clip through the pipeline.

        Returns True if any work was done.
        """
        clip = self.db.next_eligible_clip(source_id)
        if not clip:
            return False

        clip_log = log.with_fields(clip_id=clip.id, state=clip.state)

        try:
            if clip.state == PENDING:
                # Clip needs processing — but process_source handles batches
                # This path is for individual re-processing
                clip_log.info("Clip still pending — covered by process_source")
                return False

            if clip.state == PROCESSED:
                # Upload to Cloudinary
                clip_log.info("Uploading clip to Cloudinary")
                upload_clip(clip, self.config, self.db, dry_run=self.config.dry_run)
                return True

            if clip.state == CLOUDINARY_UPLOADED:
                if not self._can_publish():
                    remaining = (
                        self.config.upload_interval_seconds
                        - (time.time() - self._last_publish_time())
                    )
                    clip_log.info(
                        f"Waiting {remaining:.0f}s before next publish"
                    )
                    return False

                clip_log.info("Publishing clip to Instagram")
                publish_clip(
                    clip,
                    self.config,
                    self.db,
                    dry_run=self.config.dry_run,
                    auto_publish=self.config.auto_publish,
                )
                self._record_publish()
                return True

            if clip.state == READY_TO_PUBLISH:
                if not self.config.auto_publish:
                    clip_log.info("AUTO_PUBLISH is off; clip ready but not publishing")
                    return False
                if not self._can_publish():
                    return False

                clip_log.info("Publishing ready clip")
                from app.instagram_client import publish_container

                if clip.instagram_container_id and not self.config.dry_run:
                    media_id = publish_container(
                        self.config, clip.instagram_container_id
                    )
                    self.db.update_clip_state(
                        clip.id, PUBLISHED, instagram_media_id=media_id
                    )
                    self.db.upsert_instagram_job(
                        clip.id, media_id=media_id, status="published"
                    )
                    self._record_publish()
                    return True
                elif self.config.dry_run:
                    clip_log.info("DRY RUN: Would publish ready container")
                return False

        except (CloudinaryError, InstagramPermanentError) as e:
            clip_log.error(f"Permanent failure: {e}")
            return True  # Work was attempted
        except (InstagramError, ProcessingError) as e:
            clip_log.warning(f"Transient failure: {e}")
            return True

        return False

    def _retry_failed(self, source_id: str) -> int:
        """Retry eligible failed clips with exponential backoff."""
        eligible = self.db.get_retry_eligible(source_id, self.config.max_retries)
        retried = 0

        for clip in eligible:
            if self._should_stop():
                break

            backoff = min(2 ** clip.attempt_count * 60, 3600)
            time_since_fail = time.time() - clip.updated_at
            if time_since_fail < backoff:
                continue

            log.info(
                f"Retrying clip {clip.id} (attempt {clip.attempt_count + 1})",
                extra={"_structured": {"clip_id": clip.id, "attempt": clip.attempt_count}},
            )

            if clip.state == FAILED:
                self.db.update_clip_state(clip.id, RETRY_PENDING)
            self.db.update_clip_state(clip.id, PENDING)
            retried += 1

        return retried

    def run_worker(self) -> None:
        """
        Main scheduler loop.

        1. Download source (if needed)
        2. Process all clips
        3. Upload and publish on schedule
        4. Retry failed clips
        5. Sleep and repeat
        """
        sched_log = log.with_fields(worker_id=self.worker_id)

        if not self._acquire_lock():
            sys.exit(1)

        try:
            sched_log.info("Scheduler started", extra={"_structured": {
                "dry_run": self.config.dry_run,
                "auto_publish": self.config.auto_publish,
                "interval": self.config.upload_interval_seconds,
            }})

            # Step 1: Download source
            try:
                source_path = download_source(self.config, self.db)
            except DownloadError as e:
                sched_log.error(f"Source download failed: {e}")
                return

            source = self.db.get_source_by_drive_id(self.config.google_drive_file_id)
            if not source:
                sched_log.error("Source record not found after download")
                return
            source_id = source.id

            # Step 2: Process clips
            try:
                clip_ids = process_source(
                    source_path, self.config, self.db, source_id
                )
                sched_log.info(f"Processed {len(clip_ids)} clips")
            except ProcessingError as e:
                sched_log.error(f"Processing failed: {e}")
                return

            # Step 3: Upload and publish loop
            while not self._should_stop():
                self.db.refresh_lock("scheduler", self.worker_id)

                work_done = self._process_next_clip(source_id)

                if not work_done:
                    # Check for retries
                    retried = self._retry_failed(source_id)
                    if retried:
                        continue

                    # Check if all clips are published
                    summary = self.db.status_summary(source_id)
                    total = summary["total_clips"]
                    published = summary.get("published", 0)
                    failed_permanent = len([
                        c for c in self.db.list_clips(source_id)
                        if c.state == FAILED and c.attempt_count >= self.config.max_retries
                    ])

                    if published + failed_permanent >= total:
                        sched_log.info(
                            "All clips processed",
                            extra={"_structured": summary},
                        )
                        break

                    # Sleep before next check
                    sleep_time = min(
                        self.config.upload_interval_seconds,
                        HEARTBEAT_INTERVAL,
                    )
                    sched_log.info(
                        f"No work available. Sleeping {sleep_time}s",
                        extra={"_structured": {
                            "next_check": time.time() + sleep_time,
                            **summary,
                        }},
                    )

                    # Interruptible sleep
                    for _ in range(int(sleep_time)):
                        if self._should_stop():
                            break
                        time.sleep(1)

        finally:
            self._release_lock()
            sched_log.info("Scheduler stopped")

    def show_queue(self, source_id: str) -> dict:
        """Return a summary of the current queue state."""
        summary = self.db.status_summary(source_id)
        last_pub = self._last_publish_time()
        next_pub = last_pub + self.config.upload_interval_seconds if last_pub else None

        return {
            **summary,
            "last_publish": time.ctime(last_pub) if last_pub else "Never",
            "next_publish_after": time.ctime(next_pub) if next_pub else "Now",
            "interval_seconds": self.config.upload_interval_seconds,
        }
