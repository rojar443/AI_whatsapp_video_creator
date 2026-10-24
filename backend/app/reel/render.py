"""Renderers turn a ReelPlan into an MP4. FFmpegRenderer: 9:16 crop, grade, photo zoom,
captions, music or silence, and a bitrate that keeps the file under WhatsApp's 16 MB limit."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from app.reel.media import ffmpeg, probe
from app.reel.models import ReelPlan, RenderResult
from app.reel.playbooks import get_playbook
from app.reel.tools import Session

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]


class Renderer(ABC):
    name = "base"

    @abstractmethod
    def render(self, plan: ReelPlan, session: Session, out: str) -> RenderResult: ...


class FFmpegRenderer(Renderer):
    name = "ffmpeg"

    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30, max_mb: float = 15.0):
        self.w, self.h, self.fps, self.max_mb = width, height, fps, max_mb
        self.font = next((f for f in FONTS if os.path.exists(f)), None)

    def _caption_filter(self, text: str, workdir: Path, n: int) -> str | None:
        if not text or not self.font:
            return None
        txt = workdir / f"cap_{n:02d}.txt"
        txt.write_text(text, encoding="utf-8")  # textfile= avoids drawtext escaping problems
        return (f"drawtext=fontfile={self.font}:textfile={txt}:fontcolor=white:fontsize=64:"
                f"borderw=4:bordercolor=black@0.6:x=(w-text_w)/2:y=h*0.72")

    def render(self, plan: ReelPlan, session: Session, out: str) -> RenderResult:
        if not plan.items:
            raise ValueError("plan has no items")
        work = Path(session.workdir) / "render"
        work.mkdir(parents=True, exist_ok=True)
        grade = get_playbook(plan.genre)["grade"]
        assets = {a.id: a for a in session.assets}
        parts = []
        for n, item in enumerate(plan.items):
            shot = session.shot(item.shot_id)
            src = assets[shot.asset_id].path
            secs = item.seconds
            if shot.kind == "photo":
                frames = max(1, int(secs * self.fps))
                # one input frame; zoompan expands it into `frames` output frames (slow push-in)
                inp = ["-i", src, "-frames:v", str(frames)]
                vf = [f"scale={self.w}:{self.h}:force_original_aspect_ratio=increase,"
                      f"crop={self.w}:{self.h},"
                      f"zoompan=z='min(zoom+0.0012,1.15)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
                      f"d={frames}:s={self.w}x{self.h}:fps={self.fps}"]
            else:
                start = shot.start + max(0.0, (shot.length - secs) / 2)  # centre of the shot
                inp = ["-ss", f"{start:.2f}", "-t", f"{secs:.2f}", "-i", src]
                vf = [f"scale={self.w}:{self.h}:force_original_aspect_ratio=increase,"
                      f"crop={self.w}:{self.h},fps={self.fps}"]
            vf.append(grade)
            caption = plan.title if (n == 0 and plan.title) else item.caption
            cap = self._caption_filter(caption, work, n)
            if cap:
                vf.append(cap)
            vf.append("fade=t=in:st=0:d=0.15,setsar=1,format=yuv420p")
            part = work / f"p{n:02d}.mp4"
            ffmpeg(*inp, "-vf", ",".join(vf), "-an", "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "20", str(part))
            parts.append(part)
        lst = work / "parts.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        joined = work / "joined.mp4"
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined))
        dur = probe(str(joined))[2]
        vkbps = int(min(6000, self.max_mb * 8 * 1024 / max(dur, 1) - 128))
        music = session.brief.music_path
        audio = (["-stream_loop", "-1", "-i", music] if music
                 else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
        afade = ["-af", f"afade=t=out:st={max(dur - 1.5, 0):.2f}:d=1.5"] if music else []
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        ffmpeg("-i", str(joined), *audio, "-map", "0:v", "-map", "1:a", *afade,
               "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{vkbps}k", "-maxrate", f"{vkbps}k",
               "-bufsize", f"{vkbps * 2}k", "-c:a", "aac", "-b:a", "128k", "-shortest",
               "-movflags", "+faststart", out)
        w, h, d = probe(out)
        return RenderResult(out, w, h, round(d, 2), round(os.path.getsize(out) / 1e6, 2))
