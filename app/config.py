"""
Central configuration module.

Reads environment variables (with .env fallback for local dev),
validates required settings, and exposes a frozen Config dataclass.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _load_env() -> None:
    """Load .env file if present (local development)."""
    env_path = Path.cwd() / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def _env_required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise EnvironmentError(f"Required environment variable {name} is not set.")
    return val


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        val = int(raw)
    except ValueError:
        raise EnvironmentError(
            f"Environment variable {name} must be an integer, got: {raw!r}"
        )
    return val


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # Google Drive
    google_drive_file_id: str
    google_application_credentials: Optional[str]

    # Processing
    data_dir: Path
    clip_duration_seconds: int
    audio_language: str
    upload_interval_seconds: int
    video_crf: int
    video_preset: str
    max_retries: int
    dry_run: bool
    auto_publish: bool

    # Cloudinary
    cloudinary_cloud_name: str
    cloudinary_api_key: str
    cloudinary_api_secret: str

    # Instagram
    instagram_access_token: str
    instagram_user_id: str
    instagram_api_version: str

    # Derived paths
    sources_dir: Path = field(init=False)
    clips_dir: Path = field(init=False)
    state_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        # Validate positive numbers
        if self.clip_duration_seconds <= 0:
            raise ValueError("CLIP_DURATION_SECONDS must be positive.")
        if self.upload_interval_seconds <= 0:
            raise ValueError("UPLOAD_INTERVAL_SECONDS must be positive.")
        if self.video_crf < 0 or self.video_crf > 51:
            raise ValueError("VIDEO_CRF must be between 0 and 51.")
        if self.max_retries < 0:
            raise ValueError("MAX_RETRIES must be non-negative.")

        # Derived paths (bypass frozen)
        object.__setattr__(self, "sources_dir", self.data_dir / "sources")
        object.__setattr__(self, "clips_dir", self.data_dir / "clips")
        object.__setattr__(self, "state_dir", self.data_dir / "state")

    def ensure_directories(self) -> None:
        """Create required data directories."""
        for d in (self.sources_dir, self.clips_dir, self.state_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    """Load configuration from environment variables."""
    _load_env()

    cfg = Config(
        # Google Drive
        google_drive_file_id=_env_required("GOOGLE_DRIVE_FILE_ID"),
        google_application_credentials=_env("GOOGLE_APPLICATION_CREDENTIALS"),
        # Processing
        data_dir=Path(_env("DATA_DIR", "/data")),
        clip_duration_seconds=_env_int("CLIP_DURATION_SECONDS", 60),
        audio_language=_env("AUDIO_LANGUAGE", "hin"),
        upload_interval_seconds=_env_int("UPLOAD_INTERVAL_SECONDS", 3600),
        video_crf=_env_int("VIDEO_CRF", 23),
        video_preset=_env("VIDEO_PRESET", "veryfast"),
        max_retries=_env_int("MAX_RETRIES", 3),
        dry_run=_env_bool("DRY_RUN", True),
        auto_publish=_env_bool("AUTO_PUBLISH", False),
        # Cloudinary
        cloudinary_cloud_name=_env_required("CLOUDINARY_CLOUD_NAME"),
        cloudinary_api_key=_env_required("CLOUDINARY_API_KEY"),
        cloudinary_api_secret=_env_required("CLOUDINARY_API_SECRET"),
        # Instagram
        instagram_access_token=_env_required("INSTAGRAM_ACCESS_TOKEN"),
        instagram_user_id=_env_required("INSTAGRAM_USER_ID"),
        instagram_api_version=_env("INSTAGRAM_API_VERSION", "v21.0"),
    )
    cfg.ensure_directories()
    return cfg


def validate_config_partial() -> dict[str, str]:
    """
    Validate as many config values as possible and return a dict of
    variable-name → error-message for anything invalid or missing.
    Does not raise; useful for the validate-config command.
    """
    _load_env()
    errors: dict[str, str] = {}

    required = [
        "GOOGLE_DRIVE_FILE_ID",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_USER_ID",
    ]
    for name in required:
        if not os.environ.get(name):
            errors[name] = "Not set (required)."

    # Numeric checks
    for name, default in [
        ("CLIP_DURATION_SECONDS", "60"),
        ("UPLOAD_INTERVAL_SECONDS", "3600"),
        ("VIDEO_CRF", "23"),
        ("MAX_RETRIES", "3"),
    ]:
        raw = os.environ.get(name, default)
        try:
            v = int(raw)
            if name == "VIDEO_CRF" and not (0 <= v <= 51):
                errors[name] = "Must be 0-51."
            elif v < 0:
                errors[name] = "Must be non-negative."
            elif name in ("CLIP_DURATION_SECONDS", "UPLOAD_INTERVAL_SECONDS") and v <= 0:
                errors[name] = "Must be positive."
        except ValueError:
            errors[name] = f"Not a valid integer: {raw!r}"

    # Data dir writability
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    if data_dir.exists() and not os.access(data_dir, os.W_OK):
        errors["DATA_DIR"] = f"Directory {data_dir} is not writable."

    return errors
