"""Tests for Google Drive downloader with mocked API."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from app.downloader import (
    download_source,
    DownloadError,
    InsufficientSpaceError,
    _validate_download,
    _compute_checksum,
)


class TestValidation:
    def test_validate_existing_file(self, tmp_path):
        """Test case 1: Source already exists and is valid."""
        f = tmp_path / "test.mp4"
        f.write_bytes(b"x" * 1000)
        assert _validate_download(f, 1000) is True

    def test_validate_wrong_size(self, tmp_path):
        """Test case 4: Source file fails validation."""
        f = tmp_path / "test.mp4"
        f.write_bytes(b"x" * 500)
        assert _validate_download(f, 1000) is False

    def test_validate_missing(self, tmp_path):
        assert _validate_download(tmp_path / "missing.mp4", 1000) is False

    def test_validate_empty(self, tmp_path):
        f = tmp_path / "empty.mp4"
        f.write_bytes(b"")
        assert _validate_download(f, None) is False

    def test_checksum(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        cs = _compute_checksum(f)
        assert len(cs) == 32  # MD5 hex length


class TestDownloadReuse:
    def test_reuse_existing_source(self, config, db, tmp_path):
        """Test case 1: Reuse validated source without re-downloading."""
        source_file = config.sources_dir / "episode.mp4"
        source_file.write_bytes(b"x" * 5000)

        db.upsert_source(
            config.google_drive_file_id,
            filename="episode.mp4",
            download_status="complete",
            local_path=str(source_file),
            actual_size=5000,
            expected_size=5000,
        )

        path = download_source(config, db)
        assert path == source_file
        # Should NOT call Google API

    @patch("app.downloader._build_drive_service")
    def test_download_new_source(self, mock_build, config, db):
        """Test case 2: Source missing, requires downloading."""
        # Mock Drive service
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock file metadata
        mock_service.files.return_value.get.return_value.execute.return_value = {
            "id": config.google_drive_file_id,
            "name": "episode.mp4",
            "size": "5000",
            "mimeType": "video/mp4",
        }

        # Mock download
        mock_request = MagicMock()
        mock_service.files.return_value.get_media.return_value = mock_request

        # Simulate MediaIoBaseDownload
        with patch("app.downloader.MediaIoBaseDownload") as MockDl:
            mock_dl = MagicMock()
            MockDl.return_value = mock_dl

            # Simulate progress: two chunks then done
            status1 = MagicMock()
            status1.progress.return_value = 0.5
            status2 = MagicMock()
            status2.progress.return_value = 1.0
            mock_dl.next_chunk.side_effect = [
                (status1, False),
                (status2, True),
            ]

            # Write actual data to the temp file during open()
            import builtins
            original_open = builtins.open

            def fake_open(path, mode='r', *args, **kwargs):
                fh = original_open(path, mode, *args, **kwargs)
                if 'w' in mode and '.downloading' in str(path):
                    fh.write(b"x" * 5000)
                return fh

            with patch("builtins.open", side_effect=fake_open):
                path = download_source(config, db)

            source = db.get_source_by_drive_id(config.google_drive_file_id)
            assert source.download_status == "complete"

    def test_download_interrupted(self, config, db):
        """Test case 3: Download is interrupted."""
        with patch("app.downloader._build_drive_service") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.get.return_value.execute.return_value = {
                "id": "file_id", "name": "ep.mp4", "size": "5000",
            }

            mock_request = MagicMock()
            mock_service.files.return_value.get_media.return_value = mock_request

            with patch("app.downloader.MediaIoBaseDownload") as MockDl:
                mock_dl = MagicMock()
                MockDl.return_value = mock_dl
                mock_dl.next_chunk.side_effect = ConnectionError("Network down")

                with pytest.raises(DownloadError, match="failed"):
                    download_source(config, db)

            source = db.get_source_by_drive_id(config.google_drive_file_id)
            assert source.download_status == "failed"


class TestDiskSpace:
    def test_insufficient_space(self, config, db):
        """Test case 19: Persistent volume runs out of space."""
        with patch("app.downloader._build_drive_service") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.get.return_value.execute.return_value = {
                "id": "file_id", "name": "ep.mp4", "size": "50000000000",
            }

            with patch("shutil.disk_usage") as mock_disk:
                mock_disk.return_value = MagicMock(
                    free=100 * 1024 * 1024,  # Only 100 MB free
                )
                with pytest.raises(InsufficientSpaceError):
                    download_source(config, db)
