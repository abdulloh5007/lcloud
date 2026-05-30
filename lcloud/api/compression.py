"""Server-side image compression for uploads.

Default behaviour: any uploaded image is re-encoded at slightly-lossy
JPEG quality 85 (or kept as PNG when transparency is present). Users
can opt out via the `compress` upload form field set to false → file
goes byte-for-byte to Telegram.

Compression rules:

| Input format | compress=True (default)            | compress=False  |
|--------------|------------------------------------|-----------------|
| image/jpeg   | re-encode JPEG q=85, strip EXIF    | byte-for-byte   |
| image/png    | RGBA → keep PNG (lossless), else   | byte-for-byte   |
|              | flatten + re-encode JPEG q=85      |                 |
| image/webp   | re-encode WebP q=85                | byte-for-byte   |
| image/heic   | not supported, byte-for-byte       | byte-for-byte   |
| video/*      | not supported, byte-for-byte       | byte-for-byte   |
| anything     | byte-for-byte                      | byte-for-byte   |

The "compressed" flag stored in DB only flips True if we actually
re-encoded the bytes. Quota tracking uses the **final** size (post-
compression), so users get the storage benefit.
"""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Mime types we know how to re-encode lossy
COMPRESSIBLE_IMAGE_MIMES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
DEFAULT_JPEG_QUALITY = 85
DEFAULT_WEBP_QUALITY = 85
# Don't bother re-encoding tiny images — compression overhead exceeds savings
MIN_RECOMPRESS_BYTES = 50 * 1024  # 50 KiB


def is_compressible_mime(mime: str | None) -> bool:
    """True if our compress() function knows how to re-encode this mime."""
    if not mime:
        return False
    return mime.lower() in COMPRESSIBLE_IMAGE_MIMES


def compress_image_in_place(
    src: Path,
    *,
    mime: str,
    out: Path | None = None,
) -> tuple[Path, int, str, bool]:
    """Re-encode the file at `src` to a smaller form.

    Args:
        src:  Path to the temp file we're about to upload.
        mime: Original mime type (driver hint for output format).
        out:  Where to write the compressed result. Default: src + ".cmp".

    Returns:
        (output_path, output_size_bytes, output_mime, was_compressed)

        If we couldn't compress (file too small, format unsupported,
        Pillow error, or the result was BIGGER than original), the
        return is `(src, src.size, mime, False)` — caller uses
        original bytes unchanged.
    """
    out_path = out or src.with_suffix(src.suffix + ".cmp")

    try:
        src_size = src.stat().st_size
    except OSError:
        return src, 0, mime, False

    if src_size < MIN_RECOMPRESS_BYTES:
        return src, src_size, mime, False

    if not is_compressible_mime(mime):
        return src, src_size, mime, False

    try:
        with Image.open(src) as img:
            img.load()  # force decode now so we can catch errors here
            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )

            mime_lower = mime.lower()
            if mime_lower == "image/png" and has_alpha:
                # Keep transparency — PNG only does lossless. Strip metadata.
                rgba = img.convert("RGBA")
                rgba.save(out_path, format="PNG", optimize=True)
                out_mime = "image/png"
            elif mime_lower == "image/webp":
                # WebP supports both lossy and alpha; favour lossy for size.
                converted = img.convert("RGBA" if has_alpha else "RGB")
                converted.save(
                    out_path,
                    format="WEBP",
                    quality=DEFAULT_WEBP_QUALITY,
                    method=4,  # 0..6, higher = smaller but slower
                )
                out_mime = "image/webp"
            else:
                # JPEG fall-through: drop alpha if present, re-encode q=85.
                rgb = img.convert("RGB")
                rgb.save(
                    out_path,
                    format="JPEG",
                    quality=DEFAULT_JPEG_QUALITY,
                    optimize=True,
                    progressive=True,
                )
                out_mime = "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("compress: Pillow failed on %s: %s", src, exc)
        return src, src_size, mime, False

    try:
        out_size = out_path.stat().st_size
    except OSError:
        return src, src_size, mime, False

    if out_size >= src_size:
        # Compression didn't help — drop the bigger version, use original.
        with contextlib.suppress(OSError):
            out_path.unlink()
        return src, src_size, mime, False

    saved_pct = 100 * (1 - out_size / src_size)
    logger.info(
        "compressed %s: %d → %d bytes (%.1f%% saved)",
        src.name,
        src_size,
        out_size,
        saved_pct,
    )
    return out_path, out_size, out_mime, True


def encode_to_bytes_buffer(img: Image.Image, *, fmt: str, **kwargs: object) -> io.BytesIO:
    """Helper for in-memory encoding (used by tests)."""
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    buf.seek(0)
    return buf


__all__ = [
    "COMPRESSIBLE_IMAGE_MIMES",
    "DEFAULT_JPEG_QUALITY",
    "MIN_RECOMPRESS_BYTES",
    "compress_image_in_place",
    "encode_to_bytes_buffer",
    "is_compressible_mime",
]
