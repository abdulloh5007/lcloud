"""Server-side video compression via ffmpeg.

Default behaviour: any uploaded video gets re-encoded with H.264 +
AAC at a quality preset that's visually almost-identical but ~3-10×
smaller for typical phone-camera footage.

ffmpeg is invoked via subprocess. We pass `-y` (overwrite),
`-loglevel error` (quiet unless something breaks) and consume stdin
explicitly so it never blocks waiting on input.

Compression rules:

| Input                       | compress=True                       | compress=False  |
|-----------------------------|-------------------------------------|-----------------|
| video/mp4, mov, avi, mkv... | re-encode H.264 CRF=28 + AAC 128k   | byte-for-byte   |
| audio/*                     | not implemented yet, byte-for-byte  | byte-for-byte   |
| anything else               | byte-for-byte                       | byte-for-byte   |

If ffmpeg is missing, fails, or the result is bigger than the
original — we fall back to the original bytes (caller treats the
video as if compression was disabled). The function never raises.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

COMPRESSIBLE_VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
    "video/3gpp",
}

# CRF 28 is "good enough" — visually close to source for most phone footage.
# Lower CRF = larger file but better quality. 23 is the libx264 default;
# 28 gives ~50% smaller files at minor visible loss.
DEFAULT_CRF = 28
DEFAULT_AUDIO_BITRATE = "128k"

# Don't bother re-encoding tiny clips — overhead exceeds savings.
MIN_RECOMPRESS_BYTES = 200 * 1024  # 200 KiB

# Cap how long ffmpeg is allowed to run per upload — prevents DoS via
# malformed/streaming files. 5 min is generous for ≤500 MiB videos.
FFMPEG_TIMEOUT_SECONDS = 300


def is_compressible_video_mime(mime: str | None) -> bool:
    if not mime:
        return False
    return mime.lower() in COMPRESSIBLE_VIDEO_MIMES


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def compress_video_in_place(
    src: Path,
    *,
    mime: str,
    out: Path | None = None,
    crf: int = DEFAULT_CRF,
) -> tuple[Path, int, str, bool]:
    """Re-encode `src` to a smaller mp4. Returns (output_path, size, mime, was_compressed).

    Falls back to (src, src_size, mime, False) on any error.
    """
    out_path = out or src.with_suffix(src.suffix + ".cmp.mp4")

    try:
        src_size = src.stat().st_size
    except OSError:
        return src, 0, mime, False

    if src_size < MIN_RECOMPRESS_BYTES:
        return src, src_size, mime, False

    if not is_compressible_video_mime(mime):
        return src, src_size, mime, False

    if not ffmpeg_available():
        logger.warning("ffmpeg not available; skipping video compression")
        return src, src_size, mime, False

    cmd = [
        "ffmpeg",
        "-y",  # overwrite output
        "-loglevel", "error",
        "-nostdin",
        "-i", str(src),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", DEFAULT_AUDIO_BITRATE,
        "-movflags", "+faststart",  # let browsers stream-play
        str(out_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out on %s", src.name)
        with contextlib.suppress(OSError):
            out_path.unlink()
        return src, src_size, mime, False
    except OSError as exc:
        logger.warning("ffmpeg invocation failed: %s", exc)
        return src, src_size, mime, False

    if proc.returncode != 0:
        logger.warning(
            "ffmpeg returned %d on %s: %s",
            proc.returncode,
            src.name,
            proc.stderr.decode("utf-8", errors="replace")[:500],
        )
        with contextlib.suppress(OSError):
            out_path.unlink()
        return src, src_size, mime, False

    try:
        out_size = out_path.stat().st_size
    except OSError:
        return src, src_size, mime, False

    if out_size >= src_size:
        # Compression didn't help (already-small or already-optimised file)
        with contextlib.suppress(OSError):
            out_path.unlink()
        return src, src_size, mime, False

    saved_pct = 100 * (1 - out_size / src_size)
    logger.info(
        "compressed video %s: %d → %d bytes (%.1f%% saved)",
        src.name,
        src_size,
        out_size,
        saved_pct,
    )
    return out_path, out_size, "video/mp4", True


__all__ = [
    "COMPRESSIBLE_VIDEO_MIMES",
    "DEFAULT_CRF",
    "MIN_RECOMPRESS_BYTES",
    "compress_video_in_place",
    "ffmpeg_available",
    "is_compressible_video_mime",
]
