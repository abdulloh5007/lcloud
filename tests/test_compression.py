"""Tests for image compression at upload time."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from lcloud.api.compression import (
    DEFAULT_JPEG_QUALITY,
    MIN_RECOMPRESS_BYTES,
    compress_image_in_place,
    is_compressible_mime,
)


def _make_jpeg(path: Path, *, size_px: int = 1600, color: tuple[int, int, int] = (123, 200, 50)) -> int:
    """Create a JPEG with random noise; return file size in bytes.

    Random RGB noise compresses poorly → file ends up large enough to be
    a useful test specimen for recompression.
    """
    import secrets
    raw = bytearray(size_px * size_px * 3)
    for i in range(0, len(raw), 4096):
        raw[i:i + 4096] = secrets.token_bytes(min(4096, len(raw) - i))
    img = Image.frombytes("RGB", (size_px, size_px), bytes(raw))
    img.save(path, format="JPEG", quality=98)  # high q so we have room to compress
    return path.stat().st_size


def _make_png_with_alpha(path: Path, *, size_px: int = 800) -> int:
    import secrets
    raw = bytearray(size_px * size_px * 4)
    for i in range(0, len(raw), 4096):
        raw[i:i + 4096] = secrets.token_bytes(min(4096, len(raw) - i))
    img = Image.frombytes("RGBA", (size_px, size_px), bytes(raw))
    img.save(path, format="PNG", optimize=False)
    return path.stat().st_size


def _make_tiny_jpeg(path: Path) -> int:
    img = Image.new("RGB", (10, 10), color=(0, 0, 0))
    img.save(path, format="JPEG", quality=80)
    return path.stat().st_size


# --------------------------------------------------- mime detector


def test_is_compressible_mime() -> None:
    assert is_compressible_mime("image/jpeg") is True
    assert is_compressible_mime("image/jpg") is True
    assert is_compressible_mime("image/png") is True
    assert is_compressible_mime("image/webp") is True
    assert is_compressible_mime("image/gif") is False
    assert is_compressible_mime("video/mp4") is False
    assert is_compressible_mime("application/pdf") is False
    assert is_compressible_mime(None) is False
    assert is_compressible_mime("") is False
    # Case-insensitive
    assert is_compressible_mime("IMAGE/JPEG") is True


# --------------------------------------------------- jpeg


def test_compress_jpeg_actually_shrinks(tmp_path: Path) -> None:
    src = tmp_path / "in.jpg"
    original_size = _make_jpeg(src)
    assert original_size > MIN_RECOMPRESS_BYTES, "test premise — file must be big enough to recompress"

    out_path, out_size, out_mime, was_compressed = compress_image_in_place(
        src, mime="image/jpeg"
    )
    assert was_compressed is True
    assert out_path != src
    assert out_size < original_size
    assert out_mime == "image/jpeg"

    # Validate the result is still a readable JPEG
    with Image.open(out_path) as img:
        assert img.format == "JPEG"


def test_skip_tiny_files(tmp_path: Path) -> None:
    src = tmp_path / "tiny.jpg"
    size = _make_tiny_jpeg(src)
    assert size < MIN_RECOMPRESS_BYTES

    out_path, out_size, out_mime, was_compressed = compress_image_in_place(
        src, mime="image/jpeg"
    )
    # Tiny files: skip compression
    assert was_compressed is False
    assert out_path == src
    assert out_size == size


# --------------------------------------------------- png


def test_png_with_alpha_stays_png(tmp_path: Path) -> None:
    src = tmp_path / "alpha.png"
    original_size = _make_png_with_alpha(src)
    if original_size < MIN_RECOMPRESS_BYTES:
        pytest.skip("test image too small")

    out_path, out_size, out_mime, was_compressed = compress_image_in_place(
        src, mime="image/png"
    )
    if was_compressed:
        # Result must still preserve transparency
        assert out_mime == "image/png"
        with Image.open(out_path) as img:
            assert img.format == "PNG"
            assert img.mode in ("RGBA", "LA", "P")


# --------------------------------------------------- error paths


def test_unsupported_mime_passes_through(tmp_path: Path) -> None:
    src = tmp_path / "video.mp4"
    src.write_bytes(b"\x00\x01" * 100_000)  # fake video bytes
    out_path, out_size, out_mime, was_compressed = compress_image_in_place(
        src, mime="video/mp4"
    )
    assert was_compressed is False
    assert out_path == src
    assert out_mime == "video/mp4"


def test_corrupt_image_doesnt_crash(tmp_path: Path) -> None:
    src = tmp_path / "broken.jpg"
    src.write_bytes(b"\x00" * 100_000)  # not actually a JPEG
    out_path, out_size, out_mime, was_compressed = compress_image_in_place(
        src, mime="image/jpeg"
    )
    assert was_compressed is False
    assert out_path == src


def test_already_optimized_jpeg_no_savings(tmp_path: Path) -> None:
    """If the input is already smaller than what q=85 would produce,
    we should bail out and use the original."""
    import secrets
    src = tmp_path / "in.jpg"
    raw = bytearray(1500 * 1500 * 3)
    for i in range(0, len(raw), 4096):
        raw[i:i + 4096] = secrets.token_bytes(min(4096, len(raw) - i))
    img = Image.frombytes("RGB", (1500, 1500), bytes(raw))
    img.save(src, format="JPEG", quality=DEFAULT_JPEG_QUALITY - 5)
    original_size = src.stat().st_size

    out_path, out_size, out_mime, was_compressed = compress_image_in_place(
        src, mime="image/jpeg"
    )
    if was_compressed:
        assert out_size < original_size
    else:
        assert out_path == src
        assert out_size == original_size


# --------------------------------------------------- quality verification


def test_compression_default_quality_is_85() -> None:
    assert DEFAULT_JPEG_QUALITY == 85
