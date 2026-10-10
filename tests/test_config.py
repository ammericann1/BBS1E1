"""Tests for configuration module."""

import os
import pytest
from app.config import load_config, validate_config_partial


class TestConfig:
    def test_load_config_success(self, config):
        assert config.google_drive_file_id == "test_file_id_123"
        assert config.clip_duration_seconds == 60
        assert config.audio_language == "hin"
        assert config.dry_run is True
        assert config.auto_publish is False
        assert config.video_crf == 23

    def test_load_config_missing_required(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_DRIVE_FILE_ID", raising=False)
        with pytest.raises(EnvironmentError, match="GOOGLE_DRIVE_FILE_ID"):
            load_config()

    def test_invalid_clip_duration(self, monkeypatch):
        monkeypatch.setenv("CLIP_DURATION_SECONDS", "0")
        with pytest.raises(ValueError, match="positive"):
            load_config()

    def test_invalid_crf(self, monkeypatch):
        monkeypatch.setenv("VIDEO_CRF", "99")
        with pytest.raises(ValueError, match="0 and 51"):
            load_config()

    def test_negative_interval(self, monkeypatch):
        monkeypatch.setenv("UPLOAD_INTERVAL_SECONDS", "-1")
        with pytest.raises(ValueError, match="positive"):
            load_config()

    def test_non_integer_env(self, monkeypatch):
        monkeypatch.setenv("CLIP_DURATION_SECONDS", "abc")
        with pytest.raises(EnvironmentError, match="integer"):
            load_config()

    def test_bool_parsing_true(self, monkeypatch):
        for val in ("1", "true", "True", "YES", "yes"):
            monkeypatch.setenv("DRY_RUN", val)
            cfg = load_config()
            assert cfg.dry_run is True

    def test_bool_parsing_false(self, monkeypatch):
        for val in ("0", "false", "no", ""):
            monkeypatch.setenv("DRY_RUN", val)
            cfg = load_config()
            assert cfg.dry_run is False

    def test_derived_paths(self, config):
        assert config.sources_dir == config.data_dir / "sources"
        assert config.clips_dir == config.data_dir / "clips"
        assert config.state_dir == config.data_dir / "state"

    def test_ensure_directories(self, config):
        config.ensure_directories()
        assert config.sources_dir.is_dir()
        assert config.clips_dir.is_dir()
        assert config.state_dir.is_dir()


class TestValidatePartial:
    def test_all_set(self):
        errors = validate_config_partial()
        assert len(errors) == 0

    def test_missing_variable(self, monkeypatch):
        monkeypatch.delenv("CLOUDINARY_CLOUD_NAME", raising=False)
        errors = validate_config_partial()
        assert "CLOUDINARY_CLOUD_NAME" in errors
