"""Tests for TLS certificate file watcher."""

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from labelable.app import _watch_cert_files


class TestCertWatcher:
    """Tests for _watch_cert_files background task."""

    @pytest.fixture
    def cert_files(self):
        """Create temporary cert and key files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            certfile = Path(tmpdir) / "fullchain.pem"
            keyfile = Path(tmpdir) / "privkey.pem"
            certfile.write_text("cert-data")
            keyfile.write_text("key-data")
            yield certfile, keyfile

    async def test_exits_when_certfile_changes(self, cert_files):
        """Should call sys.exit(0) when cert file mtime changes."""
        certfile, keyfile = cert_files

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate cert renewal by touching the file
                time.sleep(0.01)
                certfile.write_text("new-cert-data")

        with (
            patch("labelable.app.asyncio.sleep", side_effect=fake_sleep),
            pytest.raises(SystemExit, match="0"),
        ):
            await _watch_cert_files(certfile, keyfile)

    async def test_exits_when_keyfile_changes(self, cert_files):
        """Should call sys.exit(0) when key file mtime changes."""
        certfile, keyfile = cert_files

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                time.sleep(0.01)
                keyfile.write_text("new-key-data")

        with (
            patch("labelable.app.asyncio.sleep", side_effect=fake_sleep),
            pytest.raises(SystemExit, match="0"),
        ):
            await _watch_cert_files(certfile, keyfile)

    async def test_continues_when_files_unchanged(self, cert_files):
        """Should keep looping when files haven't changed."""
        certfile, keyfile = cert_files

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError

        with (
            patch("labelable.app.asyncio.sleep", side_effect=fake_sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await _watch_cert_files(certfile, keyfile)

        assert call_count == 3

    async def test_handles_file_disappearing(self, cert_files):
        """Should handle OSError if cert files are temporarily missing."""
        certfile, keyfile = cert_files

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Delete the cert file to trigger OSError on next check
                certfile.unlink()
            elif call_count >= 3:
                raise asyncio.CancelledError

        with (
            patch("labelable.app.asyncio.sleep", side_effect=fake_sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await _watch_cert_files(certfile, keyfile)

        # Should have survived the OSError and kept running
        assert call_count == 3
