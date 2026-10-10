"""
Main CLI entry point.

Provides subcommands for every stage of the pipeline
and the scheduled worker.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click

from app.config import Config, load_config, validate_config_partial
from app.logging_config import get_logger, setup_logging
from app.state import StateDB, FAILED, PENDING, RETRY_PENDING

log = get_logger(__name__)


def _get_db(config: Config) -> StateDB:
    return StateDB(config.state_dir / "reel_automation.db")


# ─── CLI ─────────────────────────────────────────────────────────────

@click.group()
@click.option("--log-level", default="INFO", help="Logging level")
def cli(log_level: str) -> None:
    """Instagram Reel Automation Agent."""
    setup_logging(log_level)


@cli.command("validate-config")
def validate_config_cmd() -> None:
    """Validate environment variables and external configuration."""
    errors = validate_config_partial()
    if errors:
        click.echo("Configuration errors:")
        for var, msg in errors.items():
            click.echo(f"  [X] {var}: {msg}")
        sys.exit(1)
    else:
        click.echo("[OK] All configuration variables are valid.")


@cli.command("inspect-source")
@click.option("--file", "file_path", type=click.Path(exists=True), help="Local file to inspect")
def inspect_source_cmd(file_path: str | None) -> None:
    """Inspect the source video and its audio tracks."""
    from app.processor import inspect_media

    if file_path:
        path = Path(file_path)
    else:
        config = load_config()
        db = _get_db(config)
        source = db.get_source_by_drive_id(config.google_drive_file_id)
        if not source or not source.local_path:
            click.echo("Source not downloaded yet. Run 'download' first.")
            sys.exit(1)
        path = Path(source.local_path)

    info = inspect_media(path)
    click.echo(info.describe())


@cli.command("download")
def download_cmd() -> None:
    """Download the configured source video from Google Drive."""
    from app.downloader import download_source

    config = load_config()
    db = _get_db(config)
    try:
        path = download_source(config, db)
        click.echo(f"[OK] Source available at: {path}")
    except Exception as e:
        click.echo(f"[ERROR] Download failed: {e}")
        sys.exit(1)


@cli.command("process")
def process_cmd() -> None:
    """Generate clips from the source video without publishing."""
    from app.processor import process_source

    config = load_config()
    db = _get_db(config)

    source = db.get_source_by_drive_id(config.google_drive_file_id)
    if not source or not source.local_path:
        click.echo("Source not downloaded. Run 'download' first.")
        sys.exit(1)

    path = Path(source.local_path)
    if not path.is_file():
        click.echo(f"Source file missing: {path}")
        sys.exit(1)

    clip_ids = process_source(path, config, db, source.id)
    click.echo(f"[OK] {len(clip_ids)} clips created/verified.")


@cli.command("upload-cloudinary")
def upload_cloudinary_cmd() -> None:
    """Upload eligible processed clips to Cloudinary."""
    from app.cloudinary_client import upload_eligible_clips

    config = load_config()
    db = _get_db(config)

    source = db.get_source_by_drive_id(config.google_drive_file_id)
    if not source:
        click.echo("No source found. Run 'download' and 'process' first.")
        sys.exit(1)

    uploaded = upload_eligible_clips(
        config, db, source.id, dry_run=config.dry_run
    )
    click.echo(f"[OK] Uploaded {len(uploaded)} clips to Cloudinary.")
    if config.dry_run:
        click.echo("  (DRY RUN -- no actual uploads)")


@cli.command("publish-one")
def publish_one_cmd() -> None:
    """Publish one eligible clip to Instagram."""
    from app.instagram_client import publish_clip

    config = load_config()
    db = _get_db(config)

    source = db.get_source_by_drive_id(config.google_drive_file_id)
    if not source:
        click.echo("No source found.")
        sys.exit(1)

    clip = db.next_publishable_clip(source.id)
    if not clip:
        # Try CLOUDINARY_UPLOADED
        from app.state import CLOUDINARY_UPLOADED
        clips = db.list_clips(source.id, state=CLOUDINARY_UPLOADED)
        if clips:
            clip = clips[0]
        else:
            click.echo("No eligible clips to publish.")
            sys.exit(0)

    click.echo(f"Publishing clip {clip.id} (index {clip.clip_index})...")
    try:
        media_id = publish_clip(
            clip, config, db,
            dry_run=config.dry_run,
            auto_publish=True,  # Explicit publish-one command
        )
        if media_id:
            click.echo(f"[OK] Published! Media ID: {media_id}")
        elif config.dry_run:
            click.echo("[OK] DRY RUN: Would have published.")
        else:
            click.echo("[OK] Container ready (AUTO_PUBLISH was off, but publish-one overrides).")
    except Exception as e:
        click.echo(f"[ERROR] Publication failed: {e}")
        sys.exit(1)


@cli.command("run-worker")
def run_worker_cmd() -> None:
    """Start the scheduled worker process."""
    from app.scheduler import Scheduler

    config = load_config()
    db = _get_db(config)
    scheduler = Scheduler(config, db)
    scheduler.run_worker()


@cli.command("status")
def status_cmd() -> None:
    """Show source, clip, upload, and publication status."""
    config = load_config()
    db = _get_db(config)

    source = db.get_source_by_drive_id(config.google_drive_file_id)
    if not source:
        click.echo("No source registered.")
        return

    click.echo(f"Source: {source.filename or source.drive_file_id}")
    click.echo(f"  Status: {source.download_status}")
    click.echo(f"  Size:   {source.actual_size or 'unknown'} bytes")
    click.echo(f"  Path:   {source.local_path or 'not downloaded'}")
    click.echo()

    summary = db.status_summary(source.id)
    click.echo(f"Clips: {summary['total_clips']} total")
    for state, count in sorted(summary["clips_by_state"].items()):
        click.echo(f"  {state}: {count}")

    # Scheduler info
    last_pub = db.get_scheduler_state("last_publish_time")
    if last_pub:
        last_t = float(last_pub)
        next_t = last_t + config.upload_interval_seconds
        click.echo(f"\nLast publish:   {time.ctime(last_t)}")
        click.echo(f"Next eligible:  {time.ctime(next_t)}")
    else:
        click.echo("\nNo publications yet.")

    click.echo(f"\nDry run:        {config.dry_run}")
    click.echo(f"Auto publish:   {config.auto_publish}")


@cli.command("retry-failed")
def retry_failed_cmd() -> None:
    """Retry eligible failed jobs."""
    config = load_config()
    db = _get_db(config)

    source = db.get_source_by_drive_id(config.google_drive_file_id)
    if not source:
        click.echo("No source found.")
        sys.exit(1)

    eligible = db.get_retry_eligible(source.id, config.max_retries)
    if not eligible:
        click.echo("No eligible failed clips to retry.")
        return

    count = 0
    for clip in eligible:
        if clip.state == FAILED:
            db.update_clip_state(clip.id, RETRY_PENDING)
        db.update_clip_state(clip.id, PENDING)
        count += 1

    click.echo(f"[OK] Reset {count} clips to PENDING for retry.")


@cli.command("cleanup")
@click.option("--published-only", is_flag=True, help="Only clean published clip files")
def cleanup_cmd(published_only: bool) -> None:
    """Remove safe-to-delete temporary files."""
    from app.state import PUBLISHED

    config = load_config()
    db = _get_db(config)

    source = db.get_source_by_drive_id(config.google_drive_file_id)
    if not source:
        click.echo("No source found.")
        return

    clips = db.list_clips(source.id)
    removed = 0

    for clip in clips:
        if published_only and clip.state != PUBLISHED:
            continue
        if clip.state in (PUBLISHED, FAILED) and clip.output_path:
            p = Path(clip.output_path)
            if p.is_file() and str(p).startswith(str(config.clips_dir)):
                p.unlink()
                removed += 1
                log.info(f"Removed clip file: {p}")

    # Clean temp files
    if config.clips_dir.exists():
        for tmp in config.clips_dir.glob(".*tmp"):
            if str(tmp).startswith(str(config.clips_dir)):
                tmp.unlink()
                removed += 1

    click.echo(f"[OK] Removed {removed} files.")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
