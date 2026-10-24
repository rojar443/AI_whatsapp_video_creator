"""Built-in FFmpeg renderer (deterministic operations only — no generated scenes).

1. per moment: trim, crop to 9:16 around the subject (focus_x), photo slow zoom/pan, colour grade
2. join with the playbook transition (fade = xfade/acrossfade, or hard cuts)
3. final pass: title, burned-in captions, audio mix (narration + ducked music + original audio),
   H.264/AAC encode at a bitrate derived from duration so the file fits the WhatsApp limit
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.media import ffmpeg
from app.pipeline.contracts import PlanItem, RenderInput, RenderOutput, SourceAsset

log = logging.getLogger(__name__)
FPS = 30
GRADES = {
    "cinematic": "eq=contrast=1.08:saturation=0.88:gamma=0.97,colorbalance=rs=0.03:bs=-0.03:rh=-0.02:bh=0.04,vignette=PI/5",
    "natural": "eq=contrast=1.02:saturation=1.04",
    "vibrant": "eq=contrast=1.07:saturation=1.32",
    "minimalist": "eq=contrast=0.98:saturation=0.78:brightness=0.02",
    "documentary": "eq=contrast=1.05:saturation=0.85,vignette=PI/6",
}
MAX_KBPS = {720: 4500, 1080: 8000}
AUDIO_KBPS = 128


def _crop_filter(w: int, h: int, focus_x: float) -> str:
    fx = f"{max(0.0, min(1.0, focus_x)):.3f}"
    # cover-scale, then crop a 9:16 window centred on the subject horizontally
    return (f"scale=w='if(gt(a,{w}/{h}),-2,{w})':h='if(gt(a,{w}/{h}),{h},-2)',"
            f"crop={w}:{h}:x='min(max(iw*{fx}-{w}/2,0),iw-{w})':y='(ih-{h})/2',setsar=1")


def render_moment(item: PlanItem, asset: SourceAsset, i: int, w: int, h: int, grade: str, work: Path) -> tuple[str, float]:
    out = str(work / f"m{i:02d}.mp4")
    dur = round(item.source_end - item.source_start, 3)
    g = GRADES.get(grade, GRADES["natural"])
    if item.kind == "photo":
        frames = max(2, int(dur * FPS))
        zoom_in = i % 2 == 0
        z = "min(zoom+0.0011,1.14)" if zoom_in else "if(eq(on,1),1.14,max(zoom-0.0011,1.0))"
        x = "iw/2-(iw/zoom/2)" if zoom_in else f"(iw-iw/zoom)*on/{frames}"
        vf = (f"scale={2 * w}:{2 * h}:force_original_aspect_ratio=increase,crop={2 * w}:{2 * h},"
              f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},{g},format=yuv420p")
        ffmpeg.run([*ffmpeg.SAFE_IN, "-i", asset.file_path, "-f", "lavfi", "-t", f"{dur}",
                    "-i", "anullsrc=r=44100:cl=stereo", "-vf", vf, "-map", "0:v", "-map", "1:a", "-t", f"{dur}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", out])
        return out, dur
    vf = f"{_crop_filter(w, h, item.focus_x)},fps={FPS},{g},format=yuv420p"
    args = [*ffmpeg.SAFE_IN, "-ss", f"{item.source_start:.3f}", "-t", f"{dur}", "-i", asset.file_path]
    if asset.has_audio:
        args += ["-vf", vf, "-map", "0:v:0", "-map", "0:a:0", "-af", "aresample=44100,aformat=channel_layouts=stereo"]
    else:
        args += ["-f", "lavfi", "-t", f"{dur}", "-i", "anullsrc=r=44100:cl=stereo", "-vf", vf,
                 "-map", "0:v:0", "-map", "1:a"]
    ffmpeg.run([*args, "-t", f"{dur}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", out])
    return out, dur


def join(clips: list[tuple[str, float]], transition: str, t: float, work: Path) -> tuple[str, list[float]]:
    """Returns joined path and each moment's start time in the joined timeline."""
    out = str(work / "joined.mp4")
    starts, acc = [], 0.0
    use_fade = transition == "fade" and t > 0 and len(clips) > 1 and min(d for _, d in clips) > t * 2.2
    args = []
    for p, _ in clips:
        args += ["-i", Path(p).name]
    if len(clips) == 1:
        ffmpeg.run(["-i", Path(clips[0][0]).name, "-c", "copy", Path(out).name], cwd=str(work))
        return out, [0.0]
    if use_fade:
        fc, vprev, aprev = [], "0:v", "0:a"
        for k in range(1, len(clips)):
            acc += clips[k - 1][1] - t
            fc.append(f"[{vprev}][{k}:v]xfade=transition=fade:duration={t}:offset={acc:.3f}[v{k}]")
            fc.append(f"[{aprev}][{k}:a]acrossfade=d={t}[a{k}]")
            vprev, aprev = f"v{k}", f"a{k}"
        maps = ["-map", f"[{vprev}]", "-map", f"[{aprev}]"]
        starts = [0.0]
        s = 0.0
        for k in range(1, len(clips)):
            s += clips[k - 1][1] - t
            starts.append(round(s, 3))
    else:
        inputs = "".join(f"[{k}:v][{k}:a]" for k in range(len(clips)))
        fc = [f"{inputs}concat=n={len(clips)}:v=1:a=1[v][a]"]
        maps = ["-map", "[v]", "-map", "[a]"]
        for _, d in clips:
            starts.append(round(acc, 3))
            acc += d
    ffmpeg.run([*args, "-filter_complex", ";".join(fc), *maps, "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", "-c:a", "aac", "-b:a", "192k", Path(out).name], cwd=str(work))
    return out, starts


def final_pass(inp: RenderInput, joined: str, total: float, kbps: int, out: str) -> None:
    work = Path(inp.work_dir)
    pb_audio = inp.context.playbook.get("audio", {})
    music_mode = inp.context.music
    args = ["-i", Path(joined).name]
    idx = 1
    narr_idx = music_idx = None
    if inp.narration_path:
        args += ["-i", inp.narration_path]
        narr_idx, idx = idx, idx + 1
    if inp.music_path and music_mode == "licensed":
        args += ["-stream_loop", "-1", "-i", inp.music_path]
        music_idx, idx = idx, idx + 1

    vf = ["[0:v]null[v0]"]
    vlabel = "v0"
    if inp.title:
        tf = work / "title.txt"
        tf.write_text(ffmpeg.wrap(inp.title, 18), encoding="utf-8")
        fs = int(inp.height / 17)
        vf.append(f"[{vlabel}]drawtext=textfile='{tf.name}':font='DejaVu Sans':fontcolor=white:fontsize={fs}"
                  f":borderw=4:bordercolor=black@0.6:x=(w-text_w)/2:y=h*0.12:"
                  f"alpha='if(lt(t,0.3),0,if(lt(t,0.8),(t-0.3)/0.5,if(lt(t,2.6),1,max(0,(3.1-t)/0.5))))'[vt]")
        vlabel = "vt"
    if inp.subtitles_path:
        vf.append(f"[{vlabel}]subtitles={Path(inp.subtitles_path).name}[vs]")
        vlabel = "vs"

    af = []
    if music_mode == "none":
        orig_level = 0.0
    elif music_mode == "original":
        orig_level = 0.35 if narr_idx is not None else 1.0
    else:
        orig_level = float(pb_audio.get("original_audio_level", 0.2))
    af.append(f"[0:a]volume={orig_level}[orig]")
    mix = ["[orig]"]
    if narr_idx is not None:
        delay = int(inp.narration_offset * 1000)
        af.append(f"[{narr_idx}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={delay}|{delay},"
                  f"apad,volume=1.0,asplit=2[narr][sc]")
        mix.append("[narr]")
    if music_idx is not None:
        af.append(f"[{music_idx}:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.32,"
                  f"afade=t=in:d=1,afade=t=out:st={max(0.0, total - 1.5):.2f}:d=1.5[mus]")
        if narr_idx is not None:
            af.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]")
            mix.append("[duck]")
        else:
            mix.append("[mus]")
    elif narr_idx is not None:
        af.append("[sc]anullsink")
    af.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    fc = ";".join(vf + af)
    ffmpeg.run([*args, "-filter_complex", fc, "-map", f"[{vlabel}]", "-map", "[aout]", "-t", f"{total:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.4)}k", "-bufsize", f"{kbps * 2}k",
                "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ar", "44100", "-movflags", "+faststart",
                Path(out).name], cwd=str(work))


class Renderer:
    def run(self, inp: RenderInput) -> RenderOutput:
        work = Path(inp.work_dir)
        assets = {a.asset_id: a for a in inp.assets}
        grade = inp.context.style or inp.context.playbook.get("visual_treatment", {}).get("grade", "natural")
        clips = [render_moment(it, assets[it.asset_id], i, inp.width, inp.height, grade, work)
                 for i, it in enumerate(inp.items)]
        vt = inp.context.playbook.get("visual_treatment", {})
        joined, _ = join(clips, vt.get("transition", "cut"), float(vt.get("transition_seconds", 0)), work)
        total = ffmpeg.duration(joined)
        cap = MAX_KBPS[1080 if inp.height >= 1920 else 720]
        kbps = int(min(cap, max(700, inp.target_bytes * 8 * 0.93 / max(1.0, total) / 1000 - AUDIO_KBPS)))
        out = str(work / "reel.mp4")
        for _ in range(3):  # compress until it fits the delivery limit
            final_pass(inp, joined, total, kbps, out)
            size = Path(out).stat().st_size
            if size <= inp.target_bytes:
                break
            kbps = int(kbps * inp.target_bytes / size * 0.9)
            log.info("reel %.1f MB over target, re-encoding at %s kbps", size / 1e6, kbps)
        return RenderOutput(video_path=out, duration=ffmpeg.duration(out), width=inp.width, height=inp.height,
                            size_bytes=Path(out).stat().st_size)
