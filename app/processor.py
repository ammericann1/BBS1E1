"""
Media inspection and FFmpeg clip processing.

Uses FFprobe for inspection and FFmpeg for clip extraction with
configurable audio selection, codec handling, and Instagram-ready output.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import Config
from app.logging_config import get_logger
from app.state import (
    FAILED, PENDING, PROCESSED, PROCESSING, StateDB,
)

log = get_logger(__name__)

FFPROBE_TIMEOUT = 60  # seconds
FFMPEG_TIMEOUT = 600  # seconds per clip


class ProcessingError(Exception):
    """Raised on media processing failures."""


@dataclass
class AudioStream:
    index: int
    codec: str
    language: Optional[str]
    title: Optional[str]
    channels: int
    channel_layout: Optional[str]
    sample_rate: Optional[str]


@dataclass
class MediaInfo:
    format_name: str
    duration: float
    video_codec: str
    width: int
    height: int
    frame_rate: str
    pixel_format: Optional[str]
    bit_depth: Optional[int]
    audio_streams: list[AudioStream]

    def describe(self) -> str:
        lines = [
            f"Format:     {self.format_name}",
            f"Duration:   {self.duration:.2f}s ({self.duration / 60:.1f} min)",
            f"Video:      {self.video_codec} {self.width}x{self.height} @ {self.frame_rate} fps",
            f"Pixel fmt:  {self.pixel_format or 'unknown'}",
            f"Audio tracks ({len(self.audio_streams)}):",
        ]
        for a in self.audio_streams:
            lang = a.language or "und"
            title = f' "{a.title}"' if a.title else ""
            lines.append(
                f"  #{a.index}: {a.codec} {lang}{title} "
                f"{a.channels}ch {a.channel_layout or ''} {a.sample_rate or ''}Hz"
            )
        return "\n".join(lines)


def _run_ffprobe(file_path: Path) -> dict[str, Any]:
    """Run ffprobe and return parsed JSON output."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
            check=True,
        )
    except FileNotFoundError:
        raise ProcessingError(
            "ffprobe not found. Install FFmpeg: https://ffmpeg.org/download.html"
        )
    except subprocess.TimeoutExpired:
        raise ProcessingError(f"ffprobe timed out after {FFPROBE_TIMEOUT}s")
    except subprocess.CalledProcessError as e:
        raise ProcessingError(f"ffprobe failed: {e.stderr}")
    return json.loads(result.stdout)


def inspect_media(file_path: Path) -> MediaInfo:
    """Inspect a media file and return structured metadata."""
    if not file_path.is_file():
        raise ProcessingError(f"File not found: {file_path}")

    data = _run_ffprobe(file_path)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    # Find video stream
    video = None
    for s in streams:
        if s.get("codec_type") == "video":
            video = s
            break
    if not video:
        raise ProcessingError("No video stream found")

    # Parse audio streams
    audio_streams: list[AudioStream] = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags", {})
        audio_streams.append(AudioStream(
            index=s["index"],
            codec=s.get("codec_name", "unknown"),
            language=tags.get("language"),
            title=tags.get("title"),
            channels=s.get("channels", 0),
            channel_layout=s.get("channel_layout"),
            sample_rate=s.get("sample_rate"),
        ))

    # Parse frame rate
    fr = video.get("r_frame_rate", "0/1")
    try:
        num, den = fr.split("/")
        frame_rate_val = float(num) / float(den) if float(den) else 0
        frame_rate = f"{frame_rate_val:.2f}"
    except (ValueError, ZeroDivisionError):
        frame_rate = fr

    duration = float(fmt.get("duration", video.get("duration", 0)))

    return MediaInfo(
        format_name=fmt.get("format_name", "unknown"),
        duration=duration,
        video_codec=video.get("codec_name", "unknown"),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        frame_rate=frame_rate,
        pixel_format=video.get("pix_fmt"),
        bit_depth=int(video["bits_per_raw_sample"]) if "bits_per_raw_sample" in video else None,
        audio_streams=audio_streams,
    )


def select_audio_stream(
    info: MediaInfo, language: str
) -> AudioStream:
    """
    Select an audio stream by language code or explicit stream index.

    Raises ProcessingError if the requested language/index is not found.
    """
    # Try explicit numeric index first
    if language.isdigit():
        idx = int(language)
        for a in info.audio_streams:
            if a.index == idx:
                return a
        raise ProcessingError(
            f"Audio stream index {idx} not found. "
            f"Available: {[a.index for a in info.audio_streams]}"
        )

    # Match by language code
    matches = [a for a in info.audio_streams if a.language == language]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Prefer the first match; log the ambiguity
        log.warning(
            f"Multiple audio streams for language '{language}', using first match",
            extra={"_structured": {
                "language": language,
                "matches": [a.index for a in matches],
            }},
        )
        return matches[0]

    available = [(a.index, a.language, a.title) for a in info.audio_streams]
    raise ProcessingError(
        f"Audio language '{language}' not found. Available streams: {available}"
    )


def calculate_clip_boundaries(
    total_duration: float, clip_duration: int
) -> list[tuple[float, float]]:
    """
    Calculate clip start/end boundaries.

    Returns list of (start, end) tuples. The final clip may be shorter.
    """
    clips = []
    num_full = int(total_duration // clip_duration)
    for i in range(num_full):
        start = i * clip_duration
        end = start + clip_duration
        clips.append((float(start), float(end)))

    remainder = total_duration - (num_full * clip_duration)
    if remainder > 1.0:  # Only create a clip if remainder > 1 second
        clips.append((float(num_full * clip_duration), total_duration))

    return clips


def _clip_filename(source_id: str, clip_index: int) -> str:
    """Generate a deterministic clip filename."""
    return f"{source_id[:8]}_clip{clip_index:04d}.mp4"


def _should_stream_copy(info: MediaInfo) -> bool:
    """Determine if stream copy is viable for H.264 sources."""
    return info.video_codec in ("h264", "avc")


def generate_clip(
    source_path: Path,
    output_dir: Path,
    source_id: str,
    clip_index: int,
    start_time: float,
    end_time: float,
    audio_stream: AudioStream,
    config: Config,
    info: MediaInfo,
) -> Path:
    """
    Extract a single clip from the source video.

    Attempts stream copy for H.264 sources; falls back to re-encoding.
    Always encodes audio to AAC stereo for Instagram compatibility.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = _clip_filename(source_id, clip_index)
    output_path = output_dir / filename
    temp_path = output_dir / f"tmp_{source_id[:8]}_{clip_index:04d}.mp4"

    duration = end_time - start_time
    proc_log = log.with_fields(
        clip_index=clip_index, start=start_time, end=end_time, duration=duration
    )

    # Check disk space
    usage = shutil.disk_usage(str(output_dir))
    if usage.free < 500 * 1024 * 1024:  # 500 MB minimum
        raise ProcessingError("Insufficient disk space for clip generation")

    # Build FFmpeg command (use absolute paths to avoid Windows issues)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", str(source_path.resolve()),
        "-t", str(duration),
        "-map", "0:v:0",  # First video stream
        "-map", f"0:{audio_stream.index}",  # Selected audio stream
    ]

    # Video encoding strategy
    if _should_stream_copy(info):
        # Try stream copy first — faster but may not give exact cuts
        cmd.extend(["-c:v", "copy"])
    else:
        cmd.extend([
            "-c:v", "libx264",
            "-crf", str(config.video_crf),
            "-preset", config.video_preset,
            "-pix_fmt", "yuv420p",
        ])

    # Audio: always encode to AAC stereo for Instagram
    cmd.extend([
        "-c:a", "aac",
        "-ac", "2",
        "-b:a", "128k",
    ])

    # Fast-start for MP4
    cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(temp_path.resolve()))

    proc_log.info("Generating clip")
    start_ts = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT,
        )
        if result.returncode != 0:
            # If stream copy failed, retry with re-encoding
            if _should_stream_copy(info) and "non monotonically" in result.stderr.lower():
                proc_log.warning("Stream copy failed, falling back to re-encode")
                return _reencode_clip(
                    source_path, output_dir, source_id, clip_index,
                    start_time, end_time, audio_stream, config, temp_path, output_path
                )
            raise ProcessingError(f"FFmpeg failed: {result.stderr[-500:]}")
    except FileNotFoundError:
        raise ProcessingError(
            "ffmpeg not found. Install FFmpeg: https://ffmpeg.org/download.html"
        )
    except subprocess.TimeoutExpired:
        temp_path.unlink(missing_ok=True)
        raise ProcessingError(f"FFmpeg timed out after {FFMPEG_TIMEOUT}s")

    elapsed = time.time() - start_ts

    # Validate output
    if not temp_path.is_file() or temp_path.stat().st_size == 0:
        temp_path.unlink(missing_ok=True)
        raise ProcessingError("FFmpeg produced empty or missing output")

    _validate_clip(temp_path)

    # Atomic rename
    temp_path.replace(output_path)

    proc_log.info(
        "Clip generated",
        extra={"_structured": {
            "output": str(output_path),
            "size": output_path.stat().st_size,
            "elapsed_seconds": round(elapsed, 1),
        }},
    )
    return output_path


def _reencode_clip(
    source_path: Path,
    output_dir: Path,
    source_id: str,
    clip_index: int,
    start_time: float,
    end_time: float,
    audio_stream: AudioStream,
    config: Config,
    temp_path: Path,
    output_path: Path,
) -> Path:
    """Re-encode a clip when stream copy is not viable."""
    duration = end_time - start_time
    temp_path.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", str(source_path.resolve()),
        "-t", str(duration),
        "-map", "0:v:0",
        "-map", f"0:{audio_stream.index}",
        "-c:v", "libx264",
        "-crf", str(config.video_crf),
        "-preset", config.video_preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ac", "2",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(temp_path.resolve()),
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
    )
    if result.returncode != 0:
        temp_path.unlink(missing_ok=True)
        raise ProcessingError(f"FFmpeg re-encode failed: {result.stderr[-500:]}")

    _validate_clip(temp_path)
    temp_path.replace(output_path)
    return output_path


def _validate_clip(clip_path: Path) -> None:
    """Verify a generated clip has valid audio and video streams."""
    data = _run_ffprobe(clip_path)
    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_video:
        raise ProcessingError(f"Generated clip missing video stream: {clip_path}")
    if not has_audio:
        raise ProcessingError(f"Generated clip missing audio stream: {clip_path}")

    duration = float(data.get("format", {}).get("duration", 0))
    if duration < 0.5:
        raise ProcessingError(f"Generated clip too short ({duration:.2f}s): {clip_path}")


def process_source(
    source_path: Path,
    config: Config,
    db: StateDB,
    source_id: str,
) -> list[str]:
    """
    Process a source video into clips.

    Returns list of clip IDs that were created or already exist.
    Skips clips that are already PROCESSED or beyond.
    """
    info = inspect_media(source_path)
    audio = select_audio_stream(info, config.audio_language)

    log.info(
        "Processing source",
        extra={"_structured": {
            "source_id": source_id,
            "duration": info.duration,
            "audio_stream": audio.index,
            "audio_language": audio.language,
        }},
    )

    boundaries = calculate_clip_boundaries(info.duration, config.clip_duration_seconds)
    log.info(f"Calculated {len(boundaries)} clip boundaries")

    clip_ids = []
    for i, (start, end) in enumerate(boundaries):
        duration = end - start
        clip = db.create_clip(
            source_id=source_id,
            clip_index=i,
            start_time=start,
            end_time=end,
            duration=duration,
            audio_stream=str(audio.index),
        )
        clip_ids.append(clip.id)

        # Skip already-processed clips
        if clip.state not in (PENDING, FAILED):
            log.info(f"Clip {clip.id} already in state {clip.state}, skipping")
            continue

        # Transition to PROCESSING
        if clip.state == FAILED:
            db.update_clip_state(clip.id, "RETRY_PENDING")
            db.update_clip_state(clip.id, PENDING)

        attempt_id = db.record_attempt(clip.id, "processing", clip.attempt_count + 1)

        try:
            db.update_clip_state(clip.id, PROCESSING)
            output_path = generate_clip(
                source_path=source_path,
                output_dir=config.clips_dir,
                source_id=source_id,
                clip_index=i,
                start_time=start,
                end_time=end,
                audio_stream=audio,
                config=config,
                info=info,
            )
            db.update_clip_state(
                clip.id, PROCESSED,
                output_path=str(output_path),
                codec="h264",
            )
            db.complete_attempt(attempt_id, success=True)
        except ProcessingError as e:
            db.update_clip_state(clip.id, FAILED, last_error=str(e))
            db.complete_attempt(attempt_id, success=False, error_message=str(e))
            log.error(f"Failed to process clip {i}: {e}")

    return clip_ids
