"""
Shared test fixtures and helpers.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.state import StateDB


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch, tmp_path):
    """Set required env vars for all tests so Config doesn't raise."""
    defaults = {
        "GOOGLE_DRIVE_FILE_ID": "test_file_id_123",
        "GOOGLE_APPLICATION_CREDENTIALS": "credentials.json",
        "CLOUDINARY_CLOUD_NAME": "test_cloud",
        "CLOUDINARY_API_KEY": "test_key",
        "CLOUDINARY_API_SECRET": "test_secret",
        "INSTAGRAM_ACCESS_TOKEN": "test_token",
        "INSTAGRAM_USER_ID": "17841400000000000",
        "INSTAGRAM_API_VERSION": "v21.0",
        "DATA_DIR": str(tmp_path / "data"),
        "CLIP_DURATION_SECONDS": "60",
        "AUDIO_LANGUAGE": "hin",
        "UPLOAD_INTERVAL_SECONDS": "3600",
        "VIDEO_CRF": "23",
        "VIDEO_PRESET": "veryfast",
        "MAX_RETRIES": "3",
        "DRY_RUN": "true",
        "AUTO_PUBLISH": "false",
    }
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory structure."""
    data = tmp_path / "data"
    (data / "sources").mkdir(parents=True)
    (data / "clips").mkdir(parents=True)
    (data / "state").mkdir(parents=True)
    return data


@pytest.fixture
def db(tmp_data_dir):
    """Create a fresh test database."""
    db_path = tmp_data_dir / "state" / "test.db"
    database = StateDB(db_path)
    yield database
    database.close()


@pytest.fixture
def config(tmp_data_dir):
    """Load config pointing at tmp directories."""
    from app.config import load_config
    return load_config()


def _ffmpeg_available() -> bool:
    """Check if FFmpeg/FFprobe are available."""
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True, timeout=5, check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="FFmpeg/FFprobe not installed",
)


def create_test_video(path: Path, duration: float = 5.0, audio_lang: str = "hin") -> Path:
    """
    Create a small synthetic test video with FFmpeg.

    Generates a 320x240 H.264 video with a sine-wave audio track.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d={duration}:r=24",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2",
        "-metadata:s:a:0", f"language={audio_lang}",
        "-movflags", "+faststart",
        "-shortest",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create test video: {result.stderr}")
    return path


def create_multi_audio_video(
    path: Path, duration: float = 5.0, languages: list[str] | None = None
) -> Path:
    """Create a test video with multiple audio tracks."""
    if languages is None:
        languages = ["hin", "eng"]

    inputs = [
        "-f", "lavfi", "-i", f"color=c=red:s=320x240:d={duration}:r=24",
    ]
    maps = ["-map", "0:v"]
    audio_opts = []

    for i, lang in enumerate(languages):
        freq = 440 + i * 200
        inputs.extend([
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
        ])
        maps.extend(["-map", f"{i + 1}:a"])
        audio_opts.extend([
            f"-metadata:s:a:{i}", f"language={lang}",
            f"-metadata:s:a:{i}", f"title={lang.upper()} Audio",
        ])

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        *maps,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2",
        *audio_opts,
        "-movflags", "+faststart",
        "-shortest",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create multi-audio video: {result.stderr}")
    return path
