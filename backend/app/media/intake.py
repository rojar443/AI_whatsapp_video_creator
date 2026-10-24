"""Validate and store an incoming clip/photo (type, size, duration, codec, pixel count)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.media import ffmpeg

VIDEO_MIME = {"video/mp4": "mp4", "video/3gpp": "3gp", "video/quicktime": "mov", "video/webm": "webm"}
IMAGE_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}  # HEIC: ask for JPEG
VIDEO_CODECS = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "h263"}
IMAGE_CODECS = {"mjpeg", "png", "webp"}
_MAGIC = {
    "jpg": [b"\xff\xd8\xff"], "png": [b"\x89PNG"], "webp": [b"RIFF"], "webm": [b"\x1aE\xdf\xa3"],
}


class MediaRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class MediaInfo:
    kind: str  # video | photo
    ext: str
    duration: float | None
    width: int
    height: int
    codec: str
    has_audio: bool


def base_mime(mime: str | None) -> str:
    return (mime or "").split(";")[0].strip().lower()


def _magic_ok(data_head: bytes, ext: str) -> bool:
    if ext in ("mp4", "mov", "3gp", "heic"):
        return data_head[4:8] == b"ftyp"
    return any(data_head.startswith(m) for m in _MAGIC.get(ext, [b""]))


def inspect(path: str, mime: str | None, size: int) -> MediaInfo:
    """Raise MediaRejected with a user-friendly reason, else return probed info."""
    s = get_settings()
    m = base_mime(mime)
    if m in VIDEO_MIME:
        kind, ext, limit = "video", VIDEO_MIME[m], s.max_video_bytes
    elif m in IMAGE_MIME:
        kind, ext, limit = "photo", IMAGE_MIME[m], s.max_image_bytes
    else:
        raise MediaRejected("unsupported file type (send videos or photos)")
    if size > limit:
        raise MediaRejected(f"file is larger than {limit // (1024 * 1024)} MB")
    with Path(path).open("rb") as fh:
        head = fh.read(16)
    if not _magic_ok(head, ext):
        raise MediaRejected("file content doesn't match its type")
    try:
        info = ffmpeg.probe(path)
    except ffmpeg.FFmpegError as e:
        raise MediaRejected("file is damaged or unreadable") from e
    streams = info.get("streams", [])
    vs = [st for st in streams if st.get("codec_type") == "video"]
    if not vs:
        raise MediaRejected("no picture found in the file")
    v = vs[0]
    codec = str(v.get("codec_name") or "")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if w <= 0 or h <= 0 or w * h > s.max_image_pixels:
        raise MediaRejected("image dimensions are invalid or too large")
    allowed = VIDEO_CODECS if kind == "video" else IMAGE_CODECS
    if codec not in allowed:
        raise MediaRejected(f"unsupported codec ({codec})")
    dur = None
    if kind == "video":
        dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
        if dur < 0.5:
            raise MediaRejected("clip is too short")
        if dur > s.max_clip_seconds:
            raise MediaRejected(f"clip is longer than {int(s.max_clip_seconds)} seconds")
    rotate = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotate = abs(int(sd["rotation"])) % 180
    if rotate == 90:
        w, h = h, w
    has_audio = any(st.get("codec_type") == "audio" for st in streams)
    return MediaInfo(kind, ext, dur, w, h, codec, has_audio)
