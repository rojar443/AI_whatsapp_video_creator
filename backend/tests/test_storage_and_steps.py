"""Object storage backends and the built-in music / script / scoring pipeline steps."""
import sys
from types import SimpleNamespace

import pytest

from app import providers, storage
from app.agents.base import AgentError
from app.config import get_settings
from app.pipeline.builtin import music, scoring
from app.pipeline.builtin.script import ScriptWriter
from app.pipeline.contracts import (
    EditContext,
    MusicInput,
    PlanItem,
    ScoringInput,
    ScriptInput,
    ScriptLine,
    ScriptOutput,
    Segment,
    SegmentScore,
)


# ------------------------------------------------------------------ safe keys / local storage
@pytest.mark.parametrize("key,expected", [
    ("a/b.mp4", "a/b.mp4"), ("/a//b/", "a/b"), ("../../etc/passwd", "etc/passwd"),
    ("a\\..\\b", "a/b"), ("./x", "x"),
])
def test_safe_key(key, expected):
    assert storage._safe_key(key) == expected


@pytest.mark.parametrize("key", ["", "/", "..", "./..", "\\"])
def test_safe_key_rejects_empty(key):
    with pytest.raises(ValueError):
        storage._safe_key(key)


def test_local_storage_roundtrip(tmp_path):
    st = storage.LocalStorage(str(tmp_path / "root"))
    assert st.put_bytes("p/1/a.bin", b"hello") == 5
    assert st.exists("p/1/a.bin") and st.read_bytes("p/1/a.bin") == b"hello"
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x" * 7)
    assert st.put_file("p/1/v.mp4", str(src), "video/mp4") == 7
    assert st.local_path("p/1/v.mp4", "/ignored") == str(tmp_path / "root/p/1/v.mp4")
    assert st.presigned_url("p/1/v.mp4", 60, "v.mp4") is None
    st.delete("p/1/v.mp4")
    st.delete("p/1/v.mp4")  # idempotent
    assert not st.exists("p/1/v.mp4")


def test_local_storage_traversal_stays_inside(tmp_path):
    st = storage.LocalStorage(str(tmp_path / "root"))
    st.put_bytes("../../escape.txt", b"x")
    assert (tmp_path / "root/escape.txt").exists() and not (tmp_path / "escape.txt").exists()


def test_local_storage_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    st = storage.LocalStorage(str(root))
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="invalid storage key"):
        st.put_bytes("link/evil.txt", b"x")


# ------------------------------------------------------------------ S3 (fake boto3)
class FakeS3:
    def __init__(self):
        self.objects, self.calls = {}, []

    def upload_file(self, src, bucket, key, ExtraArgs):
        self.calls.append(("upload", bucket, key, ExtraArgs))
        self.objects[key] = open(src, "rb").read()

    def put_object(self, Bucket, Key, Body, ContentType):
        self.calls.append(("put", Bucket, Key, ContentType))
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": SimpleNamespace(read=lambda: self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404")

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def download_file(self, bucket, key, dst):
        open(dst, "wb").write(self.objects[key])

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.calls.append(("presign", op, Params, ExpiresIn))
        return f"https://s3.test/{Params['Key']}?exp={ExpiresIn}"


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    created = {}

    def client(name, **kw):
        created.update(name=name, **kw)
        return fake

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=client))
    s = get_settings()
    for k, v in dict(s3_bucket="bkt", s3_endpoint_url="http://minio:9000", s3_region="eu",
                     s3_access_key_id="AK", s3_secret_access_key="SK").items():
        monkeypatch.setattr(s, k, v)
    return fake, created


def test_s3_requires_bucket(s3, monkeypatch):
    monkeypatch.setattr(get_settings(), "s3_bucket", None)
    with pytest.raises(ValueError, match="S3_BUCKET"):
        storage.S3Storage()


def test_s3_roundtrip(s3, tmp_path):
    fake, created = s3
    st = storage.S3Storage()
    assert created == {"name": "s3", "endpoint_url": "http://minio:9000", "region_name": "eu",
                       "aws_access_key_id": "AK", "aws_secret_access_key": "SK"}
    src = tmp_path / "f.mp4"
    src.write_bytes(b"abc")
    assert st.put_file("/p/../f.mp4", str(src), "video/mp4") == 3
    assert fake.calls[0] == ("upload", "bkt", "p/f.mp4", {"ContentType": "video/mp4"})
    assert st.put_bytes("k", b"12") == 2 and st.read_bytes("k") == b"12"
    assert st.exists("k") and not st.exists("missing")
    out = st.local_path("p/f.mp4", str(tmp_path))
    assert out == str(tmp_path / "f.mp4") and (tmp_path / "f.mp4").read_bytes() == b"abc"
    st.delete("k")
    assert not st.exists("k")


def test_s3_presigned_url(s3):
    fake, _ = s3
    url = storage.S3Storage().presigned_url("p/reel.mp4", 300, "My reel.mp4")
    assert url == "https://s3.test/p/reel.mp4?exp=300"
    params = fake.calls[-1][2]
    assert params["ResponseContentDisposition"] == 'attachment; filename="My reel.mp4"'


def test_get_storage_selects_backend(s3, monkeypatch):
    storage.get_storage.cache_clear()
    try:
        monkeypatch.setattr(get_settings(), "storage_backend", "s3")
        assert isinstance(storage.get_storage(), storage.S3Storage)
        monkeypatch.setattr(get_settings(), "storage_backend", "local")
        storage.get_storage.cache_clear()
        assert isinstance(storage.get_storage(), storage.LocalStorage)
    finally:
        storage.get_storage.cache_clear()


# ------------------------------------------------------------------ music
@pytest.fixture
def music_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "music_library_dir", str(tmp_path))
    return tmp_path


def test_library_missing_manifest(music_dir):
    assert music.library() == []


def test_library_empty_manifest(music_dir):
    (music_dir / "library.yaml").write_text("")
    assert music.library() == []


def test_library_filters_invalid_rows(music_dir):
    (music_dir / "ok.mp3").write_bytes(b"x")
    (music_dir.parent / "outside.mp3").write_bytes(b"x")
    (music_dir / "library.yaml").write_text("""
- {file: ok.mp3, license: CC-BY, title: Ok}
- {file: ok.mp3}
- {file: missing.mp3, license: CC0}
- {file: ../outside.mp3, license: CC0}
- just a string
""")
    rows = music.library()
    assert [r["title"] for r in rows] == ["Ok"] and rows[0]["path"] == str((music_dir / "ok.mp3").resolve())


def test_music_picker_prefers_mood_then_energy(music_dir, monkeypatch):
    for n in "abc":
        (music_dir / f"{n}.mp3").write_bytes(b"x")
    (music_dir / "library.yaml").write_text("""
- {file: a.mp3, license: L, title: A, moods: [chill], energy: high}
- {file: b.mp3, license: L, title: B, moods: [chill, warm], energy: low}
- {file: c.mp3, license: L, title: C, moods: [chill, warm], energy: medium, artist: Z, source_url: http://s}
""")
    monkeypatch.setattr(music.random, "choice", lambda xs: xs[0] if len(xs) == 1 else pytest.fail("tie"))
    out = music.MusicPicker().run(MusicInput(moods=["chill", "warm"], energy="medium", seconds=10, work_dir="/w"))
    assert (out.title, out.artist, out.license, out.source_url) == ("C", "Z", "L", "http://s")
    assert not out.generated


def test_music_picker_random_among_ties(music_dir, monkeypatch):
    for n in "ab":
        (music_dir / f"{n}.mp3").write_bytes(b"x")
    (music_dir / "library.yaml").write_text("""
- {file: a.mp3, license: L, title: A, moods: [x]}
- {file: b.mp3, license: L, title: B, moods: [x]}
""")
    seen = []
    monkeypatch.setattr(music.random, "choice", lambda xs: seen.append([t["title"] for t in xs]) or xs[-1])
    assert music.MusicPicker().run(MusicInput(moods=["x"], seconds=5, work_dir="/w")).title == "B"
    assert seen == [["A", "B"]]


@pytest.mark.parametrize("energy,freq", [("low", "220.0"), ("high", "261.63"), ("weird", "196.0")])
def test_music_picker_generates_bed(music_dir, monkeypatch, tmp_path, energy, freq):
    calls = []
    monkeypatch.setattr(music.ffmpeg, "run", calls.append)
    out = music.MusicPicker().run(MusicInput(moods=[], energy=energy, seconds=1, work_dir=str(tmp_path)))
    assert out.generated and out.audio_path == str(tmp_path / "music.wav")
    args = " ".join(calls[0])
    assert freq in args and "d=2.00" in args and "st=0.00" in args  # fade-out clamped at 0


# ------------------------------------------------------------------ script writer
def ctx():
    return EditContext(genre="travel", playbook={})


def items():
    return [PlanItem(segment_id="s0", asset_id="a", kind="video", source_start=0, source_end=3, role="hook",
                     description="beach")]


class FakeLLM:
    is_llm = True
    supports_vision = True
    name = "fake"

    def __init__(self, script=None, scores=None, exc=None):
        self.script, self.scores, self.exc, self.frames = script, scores, exc, None

    def write_script(self, *a):
        if self.exc:
            raise self.exc
        return self.script

    def score_moments(self, segments, frames, context):
        self.frames = frames
        if self.exc:
            raise self.exc
        return list(self.scores)


def test_script_writer_uses_llm():
    s = ScriptOutput(title="T", lines=[ScriptLine(item_index=0, text="hi")])
    providers._overrides["agent"] = FakeLLM(script=s)
    assert ScriptWriter().run(ScriptInput(items=items(), total_seconds=10, context=ctx())) is s


def test_script_writer_falls_back_on_error():
    providers._overrides["agent"] = FakeLLM(exc=AgentError("down"))
    out = ScriptWriter().run(ScriptInput(items=items(), total_seconds=10, context=ctx()))
    assert isinstance(out, ScriptOutput)


def test_script_writer_rules_agent():
    out = ScriptWriter().run(ScriptInput(items=items(), total_seconds=10, context=ctx()))
    assert isinstance(out, ScriptOutput)


# ------------------------------------------------------------------ scoring
def seg(i, kind="video", start=0.0, end=3.0, frames=()):
    return Segment(segment_id=f"s{i}", asset_id="a", kind=kind, start=start, end=end, frame_paths=list(frames))


def test_heuristic_score_no_frame():
    assert scoring.heuristic_score(seg(0), None).score == 5.5  # good length bonus
    assert scoring.heuristic_score(seg(0, end=10), None).score == 5.0
    assert scoring.heuristic_score(seg(0, "photo"), None).score == 5.0


def test_heuristic_score_exposure_and_contrast():
    flat_mid = bytes([125] * 64)
    contrasty = bytes([0, 250] * 32)
    dark = bytes([0] * 64)
    s_mid = scoring.heuristic_score(seg(0, "photo"), flat_mid)
    s_con = scoring.heuristic_score(seg(0, "photo"), contrasty)
    s_dark = scoring.heuristic_score(seg(0, "photo"), dark)
    assert s_mid.score == 7.0 and s_mid.reason.startswith("well exposed")
    assert s_con.score > s_dark.score and s_dark.reason == "usable shot"
    assert 0 <= s_dark.score <= 10


def test_scorer_vision_fills_missing_with_heuristic(tmp_path):
    f = tmp_path / "f.jpg"
    f.write_bytes(b"JPEG")
    agent = FakeLLM(scores=[SegmentScore(segment_id="s0", score=9)])
    providers._overrides["agent"] = agent
    out = scoring.MomentScorer().run(ScoringInput(
        segments=[seg(0, frames=[str(f), str(f)]), seg(1, frames=[str(tmp_path / "nope.jpg")])], context=ctx()))
    assert out.scorer == "fake-vision"
    assert {s.segment_id for s in out.scores} == {"s0", "s1"}
    assert agent.frames == {"s0": [b"JPEG"], "s1": []}


def test_scorer_vision_failure_falls_back():
    providers._overrides["agent"] = FakeLLM(exc=AgentError("x"))
    out = scoring.MomentScorer().run(ScoringInput(segments=[seg(0)], context=ctx()))
    assert out.scorer == "heuristic" and out.scores[0].segment_id == "s0"


def test_scorer_heuristic_reads_frame(tmp_path, monkeypatch):
    f = tmp_path / "f.jpg"
    f.write_bytes(b"x")
    monkeypatch.setattr(scoring.ffmpeg, "gray_frames", lambda p, ts: [bytes([125] * 16)])
    out = scoring.MomentScorer().run(ScoringInput(segments=[seg(0, "photo", frames=[str(f)])], context=ctx()))
    assert out.scorer == "heuristic" and out.scores[0].score == 7.0
    monkeypatch.setattr(scoring.ffmpeg, "gray_frames", lambda p, ts: [])
    assert scoring.MomentScorer()._heuristic(seg(0, "photo", frames=[str(f)])).score == 5.0
