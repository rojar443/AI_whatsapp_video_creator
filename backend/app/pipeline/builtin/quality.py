"""Built-in quality filter: drop clips that are too dark, too shaky or near-duplicates.

Uses tiny grayscale frames from FFmpeg (32×32) — cheap, dependency-free and good enough
to catch everyday indoor/handheld problems. Thresholds are deliberately conservative and
the filter never drops every asset.
"""
from __future__ import annotations

from app.media import ffmpeg
from app.pipeline.contracts import AssetQuality, QualityInput, QualityOutput, SourceAsset

DARK_MEAN = 40  # 0-255 average luma
SHAKE_DIFF = 30  # mean abs difference between frames 0.12 s apart
BLUR_ENERGY = 6.0  # mean gradient magnitude
DUP_HAMMING = 5  # of 64 bits


def mean(frame: bytes) -> float:
    return sum(frame) / len(frame)


def abs_diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=False)) / len(a)


def gradient_energy(frame: bytes, size: int = 32) -> float:
    total = 0
    for y in range(size - 1):
        row = y * size
        for x in range(size - 1):
            p = frame[row + x]
            total += abs(p - frame[row + x + 1]) + abs(p - frame[row + size + x])
    return total / ((size - 1) ** 2)


def ahash(frame: bytes, size: int = 32) -> int:
    """8×8 average hash from a 32×32 frame."""
    cell = size // 8
    blocks = []
    for by in range(8):
        for bx in range(8):
            s = 0
            for y in range(by * cell, (by + 1) * cell):
                s += sum(frame[y * size + bx * cell:y * size + (bx + 1) * cell])
            blocks.append(s / (cell * cell))
    avg = sum(blocks) / 64
    bits = 0
    for i, v in enumerate(blocks):
        if v > avg:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class QualityFilter:
    def analyse(self, a: SourceAsset) -> tuple[AssetQuality, int | None]:
        flags: list[str] = []
        if a.kind == "photo":
            frames = ffmpeg.gray_frames(a.file_path, [0.0])
            shake = 0.0
        else:
            d = a.duration or 1.0
            times = [d * f for f in (0.15, 0.35, 0.55, 0.75)]
            frames = ffmpeg.gray_frames(a.file_path, times)
            pairs = ffmpeg.gray_frames(a.file_path, [min(d - 0.05, t + 0.12) for t in times])
            diffs = [abs_diff(x, y) for x, y in zip(frames, pairs, strict=False)]
            shake = sorted(diffs)[len(diffs) // 2] if diffs else 0.0  # median ignores one scene cut
        if not frames:
            return AssetQuality(asset_id=a.asset_id, keep=False, score=0, reason="no readable frames"), None
        brightness = sum(mean(f) for f in frames) / len(frames)
        energy = sum(gradient_energy(f) for f in frames) / len(frames)
        if brightness < DARK_MEAN:
            flags.append("dark")
        if shake > SHAKE_DIFF:
            flags.append("shaky")
        if energy < BLUR_ENERGY:
            flags.append("blurry")
        score = 1.0
        score -= max(0.0, (DARK_MEAN + 30 - brightness) / 140)
        score -= max(0.0, (shake - SHAKE_DIFF / 2) / 80)
        score -= 0.15 if "blurry" in flags else 0.0
        if a.kind == "video" and (a.duration or 0) < 1.0:
            flags.append("too_short")
        score = round(max(0.0, min(1.0, score)), 3)
        keep = not ({"dark", "shaky", "too_short"} & set(flags))
        reason = ", ".join(flags) if flags else None
        mid = frames[len(frames) // 2]
        return AssetQuality(asset_id=a.asset_id, keep=keep, score=score, flags=flags, reason=reason), ahash(mid)

    def run(self, inp: QualityInput) -> QualityOutput:
        results: list[AssetQuality] = []
        hashes: list[tuple[int, AssetQuality]] = []
        for a in sorted(inp.assets, key=lambda x: x.order_index):
            q, h = self.analyse(a)
            if h is not None:
                for other_hash, other in hashes:
                    if other.keep and hamming(h, other_hash) <= DUP_HAMMING:
                        # keep the technically better one of the pair
                        loser, winner = (q, other) if q.score <= other.score else (other, q)
                        loser.keep = False
                        loser.flags = [*loser.flags, "duplicate"]
                        loser.duplicate_of = winner.asset_id
                        loser.reason = f"near-duplicate of another {('clip' if a.kind == 'video' else 'photo')}"
                        break
                hashes.append((h, q))
            results.append(q)
        if results and not any(r.keep for r in results):
            for r in sorted(results, key=lambda r: r.score, reverse=True)[:3]:
                r.keep = True
                r.reason = (r.reason or "") + " (kept: no better footage)"
        return QualityOutput(results=results)
