"""Tests for ffmpeg-based video compression."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lcloud.api.compression_video import (
    COMPRESSIBLE_VIDEO_MIMES,
    MIN_RECOMPRESS_BYTES,
    compress_video_in_place,
    ffmpeg_available,
    is_compressible_video_mime,
)

# Skip the heavyweight roundtrip if ffmpeg isn't installed in the env.
ffmpeg_present = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg binary not installed"
)


def test_mime_detector() -> None:
    assert is_compressible_video_mime("video/mp4") is True
    assert is_compressible_video_mime("VIDEO/QUICKTIME") is True
    assert is_compressible_video_mime("image/jpeg") is False
    assert is_compressible_video_mime(None) is False
    assert is_compressible_video_mime("") is False


def test_skip_tiny_video(tmp_path: Path) -> None:
    src = tmp_path / "small.mp4"
    src.write_bytes(b"\x00" * 1000)  # below threshold
    assert src.stat().st_size < MIN_RECOMPRESS_BYTES
    out, size, mime, was = compress_video_in_place(src, mime="video/mp4")
    assert was is False
    assert out == src


def test_unsupported_mime_passes_through(tmp_path: Path) -> None:
    src = tmp_path / "x.iso"
    src.write_bytes(b"\xFF" * (MIN_RECOMPRESS_BYTES + 1000))
    out, _size, mime, was = compress_video_in_place(src, mime="application/x-iso")
    assert was is False
    assert out == src
    assert mime == "application/x-iso"


def test_corrupt_video_doesnt_crash(tmp_path: Path) -> None:
    src = tmp_path / "garbage.mp4"
    # Random bytes; ffmpeg will reject as not-a-video and exit non-zero
    src.write_bytes(b"\x00\x01\x02\x03" * (MIN_RECOMPRESS_BYTES // 4 + 1000))
    out, _, _, was = compress_video_in_place(src, mime="video/mp4")
    assert was is False
    assert out == src


@ffmpeg_present
def test_real_video_compression_roundtrip(tmp_path: Path) -> None:
    """Generate a small synthetic video with ffmpeg, compress it, verify."""
    src = tmp_path / "test.mp4"

    # Synthetic 5-second 320x240 video at high bitrate so it has room to shrink.
    gen = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", "testsrc=duration=5:size=320x240:rate=15",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-b:v", "2000k",  # high bitrate → larger file
            str(src),
        ],
        capture_output=True,
        timeout=60,
    )
    if gen.returncode != 0:
        pytest.skip(f"could not synthesize video: {gen.stderr.decode()[:200]}")

    original_size = src.stat().st_size
    if original_size < MIN_RECOMPRESS_BYTES:
        pytest.skip(f"synthetic video too small: {original_size}b")

    out, size, mime, was = compress_video_in_place(src, mime="video/mp4")
    assert was is True
    assert out != src
    assert size < original_size
    assert mime == "video/mp4"

    # Result should be a valid mp4
    probe = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(out), "-f", "null", "-"],
        capture_output=True,
        timeout=30,
    )
    assert probe.returncode == 0


def test_compressible_set_is_consistent() -> None:
    assert "video/mp4" in COMPRESSIBLE_VIDEO_MIMES
    assert all(m.startswith("video/") for m in COMPRESSIBLE_VIDEO_MIMES)


def test_ffmpeg_missing_path_safe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If ffmpeg is missing entirely, we should fall back gracefully, not crash."""
    src = tmp_path / "x.mp4"
    src.write_bytes(b"\x00" * (MIN_RECOMPRESS_BYTES + 1000))
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    out, _, _, was = compress_video_in_place(src, mime="video/mp4")
    assert was is False
    assert out == src
