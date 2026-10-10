"""Tests for media processor module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.processor import (
    ProcessingError,
    calculate_clip_boundaries,
    inspect_media,
    select_audio_stream,
    process_source,
    MediaInfo,
    AudioStream,
)
from tests.conftest import requires_ffmpeg, create_test_video, create_multi_audio_video


class TestClipBoundaries:
    def test_exact_division(self):
        clips = calculate_clip_boundaries(300.0, 60)
        assert len(clips) == 5
        assert clips[0] == (0.0, 60.0)
        assert clips[4] == (240.0, 300.0)

    def test_final_partial_clip(self):
        """Test case 8: Final clip is shorter than configured duration."""
        clips = calculate_clip_boundaries(150.0, 60)
        assert len(clips) == 3
        assert clips[2] == (120.0, 150.0)  # 30s partial

    def test_very_short_remainder_skipped(self):
        # Remainder < 1s should be skipped
        clips = calculate_clip_boundaries(120.5, 60)
        assert len(clips) == 2  # 0.5s too short

    def test_single_clip(self):
        clips = calculate_clip_boundaries(30.0, 60)
        assert len(clips) == 1
        assert clips[0] == (0.0, 30.0)

    def test_exact_one_clip(self):
        clips = calculate_clip_boundaries(60.0, 60)
        assert len(clips) == 1
        assert clips[0] == (0.0, 60.0)


class TestAudioSelection:
    def _make_info(self, streams: list[AudioStream]) -> MediaInfo:
        return MediaInfo(
            format_name="matroska",
            duration=300.0,
            video_codec="h264",
            width=1920,
            height=1080,
            frame_rate="23.98",
            pixel_format="yuv420p",
            bit_depth=8,
            audio_streams=streams,
        )

    def test_select_by_language(self):
        """Test case 5: Requested audio language is present."""
        streams = [
            AudioStream(1, "aac", "eng", "English", 2, "stereo", "48000"),
            AudioStream(2, "aac", "hin", "Hindi", 2, "stereo", "48000"),
        ]
        info = self._make_info(streams)
        selected = select_audio_stream(info, "hin")
        assert selected.index == 2
        assert selected.language == "hin"

    def test_language_not_found(self):
        """Test case 6: Requested audio language is absent."""
        streams = [
            AudioStream(1, "aac", "eng", "English", 2, "stereo", "48000"),
        ]
        info = self._make_info(streams)
        with pytest.raises(ProcessingError, match="not found"):
            select_audio_stream(info, "hin")

    def test_multiple_tracks(self):
        """Test case 7: Source has multiple audio tracks."""
        streams = [
            AudioStream(1, "aac", "hin", "Hindi 1", 2, "stereo", "48000"),
            AudioStream(2, "aac", "eng", "English", 6, "5.1", "48000"),
            AudioStream(3, "aac", "hin", "Hindi 2", 2, "stereo", "48000"),
        ]
        info = self._make_info(streams)
        selected = select_audio_stream(info, "hin")
        assert selected.index == 1  # First match

    def test_select_by_index(self):
        streams = [
            AudioStream(1, "aac", "eng", None, 2, "stereo", "48000"),
            AudioStream(2, "aac", "hin", None, 2, "stereo", "48000"),
        ]
        info = self._make_info(streams)
        selected = select_audio_stream(info, "2")
        assert selected.index == 2

    def test_invalid_index(self):
        streams = [
            AudioStream(1, "aac", "eng", None, 2, "stereo", "48000"),
        ]
        info = self._make_info(streams)
        with pytest.raises(ProcessingError, match="not found"):
            select_audio_stream(info, "99")


@requires_ffmpeg
class TestInspectMedia:
    def test_inspect_valid_video(self, tmp_path):
        video = tmp_path / "test.mp4"
        create_test_video(video, duration=3.0, audio_lang="hin")
        info = inspect_media(video)
        assert info.video_codec == "h264"
        assert info.width == 320
        assert info.height == 240
        assert info.duration > 2.0
        assert len(info.audio_streams) >= 1

    def test_inspect_nonexistent_file(self):
        with pytest.raises(ProcessingError, match="not found"):
            inspect_media(Path("/nonexistent/video.mp4"))

    def test_inspect_multi_audio(self, tmp_path):
        video = tmp_path / "multi.mkv"
        create_multi_audio_video(video, duration=3.0, languages=["hin", "eng"])
        info = inspect_media(video)
        assert len(info.audio_streams) >= 2

    def test_describe_output(self, tmp_path):
        video = tmp_path / "test.mp4"
        create_test_video(video, duration=3.0)
        info = inspect_media(video)
        desc = info.describe()
        assert "h264" in desc.lower() or "H.264" in desc
        assert "320x240" in desc


@requires_ffmpeg
class TestClipGeneration:
    def test_generate_clips(self, tmp_path, config, db):
        """Full clip generation from synthetic source."""
        video = tmp_path / "data" / "sources" / "test.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        create_test_video(video, duration=10.0, audio_lang="hin")

        source = db.upsert_source(
            "test_file_id_123",
            filename="test.mp4",
            download_status="complete",
            local_path=str(video),
        )

        # Use 5s clips for the test
        import os
        os.environ["CLIP_DURATION_SECONDS"] = "5"
        from app.config import load_config
        test_config = load_config()

        clip_ids = process_source(video, test_config, db, source.id)
        assert len(clip_ids) == 2  # 10s / 5s = 2 clips

        # Verify clips exist and are valid
        for cid in clip_ids:
            clip = db.get_clip(cid)
            assert clip.state == "PROCESSED"
            assert clip.output_path is not None
            assert Path(clip.output_path).is_file()

    def test_ffmpeg_failure(self, tmp_path, config, db):
        """Test case 9: FFmpeg fails while processing a clip."""
        # Create a corrupt file
        bad_file = tmp_path / "bad.mp4"
        bad_file.write_bytes(b"not a video file at all")

        with pytest.raises(ProcessingError):
            inspect_media(bad_file)
