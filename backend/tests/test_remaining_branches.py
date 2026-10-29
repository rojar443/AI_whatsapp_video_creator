"""Last scattered branches: pipeline runner failures, playbook loading, rules phrasings, preferences,
reply templates, credits and account merging."""
import logging
from pathlib import Path

import pytest
import yaml

from app import playbooks
from app.agents.base import USAGE_SINK, AgentContext, AgentError, UsageInfo
from app.agents.rules import RulesAgentProvider
from app.config import get_settings
from app.conversation import preferences as P
from app.conversation import replies
from app.media.ffmpeg import OutputReport
from app.models import (Conversation, GenerationJob, GenrePlaybook, ProjectAsset, UsageRecord, User, VideoPreferences,
                        VideoProject, WhatsAppAccount, utcnow)
from app.pipeline import contracts as C
from app.pipeline import run
from app.services import accounts, credits


# --------------------------------------------------------------------------- rules agent
@pytest.fixture
def agent():
    return RulesAgentProvider()


def analyse(agent, text, state="collecting_media", **ctx):
    return agent.analyze_message(text, AgentContext(state=state, **ctx))


def test_named_song_requests_are_flagged(agent):
    a = analyse(agent, "use the song by taylor swift")
    assert a.fields["music"] == "licensed"
    assert any("commercial songs" in u for u in a.unsupported)


@pytest.mark.parametrize("text,field,value", [
    ("add captions please", "captions", True),
    ("make it half a minute", "duration", "30"),
    ("make it a minute long", "duration", "60"),
    ("vertical tiktok style", "format", "vertical"),
    ("standard quality is fine", "quality", "standard"),
])
def test_rule_extractions(agent, text, field, value):
    assert analyse(agent, text).fields[field] == value


def test_empty_message_is_other(agent):
    a = analyse(agent, "   ")
    assert a.intent == "other" and a.fields == {}


@pytest.mark.parametrize("text,state,intent", [
    ("send the link again", "completed", "resend_link"),
    ("love it, thanks", "completed", "approve"),
    ("you choose", "collecting_media", "provide_info"),
])
def test_rule_intents(agent, text, state, intent):
    assert analyse(agent, text, state=state).intent == intent


def test_thanks_before_completion_is_not_approval(agent):
    assert analyse(agent, "love it, thanks", state="collecting_media").intent != "approve"


def test_base_agent_has_no_vision(agent):
    with pytest.raises(AgentError, match="no vision support"):
        agent.score_moments([], {}, None)


# --------------------------------------------------------------------------- preferences
def test_label_booleans_and_none():
    assert P.label(True) == "On" and P.label(False) == "Off" and P.label(None) == "—"


def test_coerce_unknown_field_and_bad_bool():
    assert P.coerce_field("not_a_field", "x") is None
    assert P.coerce_field("captions", "yes") is None
    assert P.coerce_field("captions", False) is False


def test_instructions_accumulate_without_repeats():
    prefs = P.Prefs()
    assert P.apply_user_values(prefs, {"instructions": "start with the beach"}) == ["instructions"]
    assert P.apply_user_values(prefs, {"instructions": "Start with the BEACH"}) == []  # already there
    assert P.apply_user_values(prefs, {"instructions": "end on the sunset", "vibe": "nonsense"}) == ["instructions"]
    assert prefs.get("instructions") == "start with the beach; end on the sunset"
    assert "vibe" not in prefs.values


# --------------------------------------------------------------------------- replies
def test_reply_with_missing_fact_returns_raw_template():
    tpl = replies.TEMPLATES["clarify"]
    assert replies.render("definitely-not-a-purpose") == tpl
    key = next(k for k, v in replies.TEMPLATES.items() if "{" in v and k not in replies.NO_REGISTRATION)
    assert replies.render(key, {}) == replies.TEMPLATES[key]


# --------------------------------------------------------------------------- credits / accounts
@pytest.fixture
def user(db):
    u = User(name="Asha", credits_balance=5)
    db.add(u)
    db.commit()
    return u


def _job(db, user):
    p = VideoProject(user_id=user.id, last_media_at=utcnow())
    db.add(p)
    db.flush()
    j = GenerationJob(project_id=p.id, user_id=user.id, revision=1, idempotency_key=f"{p.id}:r1")
    db.add(j)
    db.flush()
    return j


def test_reserve_is_idempotent(db, user):
    j = _job(db, user)
    assert credits.reserve(db, j, 2)
    assert credits.reserve(db, j, 2)  # same run → no second charge
    db.refresh(user)
    assert user.credits_balance == 3


def test_grant_with_key_is_idempotent(db, user):
    credits.grant(db, user, 4, key="welcome")
    credits.grant(db, user, 4, key="welcome")
    db.refresh(user)
    assert user.credits_balance == 9


def test_merge_anonymous_noops(db, user):
    assert accounts.merge_anonymous(db, user, user) == 0
    other = User(name="B")
    db.add(other)
    db.flush()
    assert accounts.merge_anonymous(db, other, user) == 0  # not anonymous


# --------------------------------------------------------------------------- playbooks
@pytest.fixture
def pb_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "playbook_dir", str(tmp_path))
    playbooks.load_all.cache_clear()
    yield tmp_path
    playbooks.load_all.cache_clear()


def _travel_yaml():
    return yaml.safe_load(next(Path("playbooks").glob("travel*.y*ml")).read_text())


def test_empty_playbook_dir_fails_loudly(pb_dir):
    with pytest.raises(RuntimeError, match="no playbooks found"):
        playbooks.load_all()


def test_beat_shares_must_sum_to_one(pb_dir):
    data = _travel_yaml()
    data["structure"]["beats"][0]["share"] += 0.5
    (pb_dir / "bad.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="beat shares sum"):
        playbooks.load_all()


def test_changed_playbook_without_version_bump_is_logged(db, caplog):
    playbooks.sync_to_db(db)
    row = db.query(GenrePlaybook).filter_by(genre="travel", is_active=True).one()
    row.checksum = "stale"
    db.commit()
    with caplog.at_level(logging.ERROR):
        playbooks.sync_to_db(db)
    assert "changed without a version bump" in caplog.text


def test_playbook_prompt_data_is_json():
    import json
    pb = playbooks.get("travel")
    assert json.loads(pb.as_prompt_data())["genre"] == "travel"


# --------------------------------------------------------------------------- pipeline runner
def test_record_usage_writes_llm_costs(db, user):
    run._record_usage(db, user.id, [UsageInfo(provider="gemini", model="m", input_tokens=10, output_tokens=5)])
    db.flush()
    rec = db.query(UsageRecord).filter_by(kind="llm").one()
    assert rec.quantity == 15 and rec.unit == "tokens" and rec.provider == "gemini"


def test_fit_narration_speeds_up_long_audio(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(run.ffmpeg, "duration", lambda p: 12.0)
    monkeypatch.setattr(run.ffmpeg, "run", lambda args: seen.append(args))
    words = [C.WordTiming(word="hi", start=1.2, end=2.4)]
    out, w = run.fit_narration("/n.wav", 10.0, words, tmp_path)
    assert out.endswith("narration_fit.wav") and "atempo=1.2000" in seen[0]
    assert w[0].start == pytest.approx(1.0) and w[0].end == pytest.approx(2.0)


def test_fit_narration_caps_speedup(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(run.ffmpeg, "duration", lambda p: 40.0)
    monkeypatch.setattr(run.ffmpeg, "run", lambda args: seen.append(args))
    run.fit_narration("/n.wav", 10.0, [], tmp_path)
    assert "atempo=1.3000" in seen[0]


def test_fit_narration_leaves_short_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(run.ffmpeg, "duration", lambda p: 5.0)
    monkeypatch.setattr(run.ffmpeg, "run", lambda args: pytest.fail("should not re-encode"))
    assert run.fit_narration("/n.wav", 10.0, [], tmp_path) == ("/n.wav", [])


@pytest.fixture
def project(db, user, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "work_dir", lambda name: tmp_path)
    wa = WhatsAppAccount(wa_id="15550001111", user_id=user.id, status="linked")
    db.add(wa)
    db.flush()
    conv = Conversation(whatsapp_account_id=wa.id, user_id=user.id)
    db.add(conv)
    db.flush()
    p = VideoProject(user_id=user.id, conversation_id=conv.id, last_media_at=utcnow(), title="Trip")
    db.add(p)
    db.flush()
    row = VideoPreferences(project_id=p.id, revision=1, field_sources={}, genre="travel")
    db.add(row)
    db.commit()
    return p, row


def _asset():
    return C.SourceAsset(asset_id="a0", kind="video", order_index=0, file_path="/a.mp4", duration=6,
                         width=1920, height=1080, has_audio=True)


def test_build_plan_without_assets(db, project, monkeypatch):
    monkeypatch.setattr(run, "source_assets", lambda *a: [])
    with pytest.raises(ValueError, match="no usable clips or photos"):
        run.build_plan(db, *project)


def test_build_plan_without_moments(db, project, monkeypatch):
    monkeypatch.setattr(run, "source_assets", lambda *a: [_asset()])
    seg = C.Segment(segment_id="a0:0", asset_id="a0", kind="video", start=0, end=4)

    def step(name, inp, used):
        return {"quality_filter": C.QualityOutput(results=[C.AssetQuality(asset_id="a0", keep=True, score=1)]),
                "scene_split": C.SceneSplitOutput(segments=[seg]),
                "moment_scoring": C.ScoringOutput(scores=[]),
                "sequencing": C.SequencingOutput(items=[], total_seconds=0)}[name]
    monkeypatch.setattr(run, "run_step", step)
    with pytest.raises(ValueError, match="couldn't find usable moments"):
        run.build_plan(db, *project)


def test_build_plan_lists_rejected_uploads(db, project, monkeypatch):
    p, row = project
    db.add(ProjectAsset(project_id=p.id, user_id=p.user_id, kind="video", media_id="bad", status="rejected",
                        reject_reason=None))
    db.commit()
    monkeypatch.setattr(run, "source_assets", lambda *a: [_asset()])
    seg = C.Segment(segment_id="a0:0", asset_id="a0", kind="video", start=0, end=4)
    item = C.PlanItem(segment_id="a0:0", asset_id="a0", kind="video", source_start=0, source_end=4, role="hook")

    def step(name, inp, used):
        if name in ("sequencing", "scene_split") and USAGE_SINK.get() is not None:
            USAGE_SINK.get().append(UsageInfo(provider="gemini", model=None, input_tokens=1, output_tokens=1))
        return {"quality_filter": C.QualityOutput(results=[C.AssetQuality(asset_id="a0", keep=True, score=1)]),
                "scene_split": C.SceneSplitOutput(segments=[seg]),
                "moment_scoring": C.ScoringOutput(scores=[C.SegmentScore(segment_id="a0:0", score=8)]),
                "sequencing": C.SequencingOutput(items=[item], total_seconds=4),
                "script": C.ScriptOutput(title="T", lines=[]),
                "music": C.MusicOutput(audio_path=None, title="Song", artist="Lib")}[name]
    monkeypatch.setattr(run, "run_step", step)
    plan = run.build_plan(db, p, row)
    bad = db.query(ProjectAsset).filter_by(media_id="bad").one()
    assert [d for d in plan["dropped"] if d["asset_id"] == bad.id][0]["reason"] == "unusable"
    assert plan["music_title"] == "Song by Lib"
    assert db.query(UsageRecord).filter_by(kind="llm").count() == 2


def _render_setup(db, project, plan):
    p, row = project
    row.plan = plan
    row.captions = False
    row.narration = "none"
    j = GenerationJob(project_id=p.id, user_id=p.user_id, revision=1, idempotency_key=f"{p.id}:r1")
    db.add(j)
    db.commit()
    return j


ITEM = {"segment_id": "a0:0", "asset_id": "a0", "kind": "video", "source_start": 0, "source_end": 4, "role": "hook"}


def test_render_job_rejects_empty_plan(db, project, monkeypatch):
    monkeypatch.setattr(run, "source_assets", lambda *a: [])
    j = _render_setup(db, project, {"items": []})
    with pytest.raises(run.RenderFailure) as e:
        run.render_job(db, j, lambda s: None)
    assert e.value.code == "empty_plan"


def test_render_job_reuses_stored_music_and_validates_output(db, project, monkeypatch, tmp_path):
    music = tmp_path / "song.mp3"
    music.write_bytes(b"x")
    j = _render_setup(db, project, {"items": [ITEM], "total_seconds": 4, "music": {"audio_path": str(music)}})
    monkeypatch.setattr(run, "source_assets", lambda *a: [_asset()])
    seen = {}

    def step(name, inp, used):
        assert name == "render", f"unexpected step {name}"  # music comes from the plan, not regenerated
        seen["music"] = inp.music_path
        return C.RenderOutput(video_path=str(tmp_path / "reel.mp4"), duration=4, width=720, height=1280, size_bytes=1)
    monkeypatch.setattr(run, "run_step", step)
    monkeypatch.setattr(run.ffmpeg, "validate_mp4",
                        lambda *a, **k: OutputReport(ok=False, problems=["no audio stream", "too short"]))
    with pytest.raises(run.RenderFailure) as e:
        run.render_job(db, j, lambda s: None)
    assert seen["music"] == str(music)
    assert e.value.code == "invalid_output" and "no audio stream; too short" in str(e.value)
