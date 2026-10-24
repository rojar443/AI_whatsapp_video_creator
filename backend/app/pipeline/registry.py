"""Configurable pipeline steps ("processing agents").

Each step is configured with one of:
  builtin                         – the implementation in app/pipeline/builtin
  python:my_pkg.my_mod:MyStep     – a class with `run(input_model) -> output_model`
  https://my-agent.example/score  – an HTTP service speaking the JSON contract

Source of configuration (later wins): defaults → PIPELINE_CONFIG_FILE (YAML) → PIPELINE_<STEP> env.
YAML form:
    steps:
      moment_scoring: https://agents.internal/score
      script: python:my_agents.script:ScriptAgent
    fallback_to_builtin: true
"""
from __future__ import annotations

import importlib
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.pipeline.contracts import CONTRACT_VERSION, STEP_IO, STEPS

log = logging.getLogger(__name__)


class StepError(Exception):
    def __init__(self, message: str, retryable: bool = False, code: str = "step_failed"):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


class Step:
    name = "base"
    impl = "builtin"

    def run(self, inp: BaseModel) -> BaseModel:  # pragma: no cover - interface
        raise NotImplementedError


_BUILTINS = {
    "quality_filter": "app.pipeline.builtin.quality:QualityFilter",
    "scene_split": "app.pipeline.builtin.scenes:SceneSplitter",
    "moment_scoring": "app.pipeline.builtin.scoring:MomentScorer",
    "sequencing": "app.pipeline.builtin.sequencing:Sequencer",
    "script": "app.pipeline.builtin.script:ScriptWriter",
    "narration": "app.pipeline.builtin.narration:Narrator",
    "captions": "app.pipeline.builtin.captions:CaptionBuilder",
    "music": "app.pipeline.builtin.music:MusicPicker",
    "render": "app.pipeline.builtin.render:Renderer",
}
_test_overrides: dict[str, Step] = {}


def configured_specs() -> dict[str, str]:
    s = get_settings()
    specs = {step: "builtin" for step in STEPS}
    if s.pipeline_config_file and Path(s.pipeline_config_file).exists():
        data = yaml.safe_load(Path(s.pipeline_config_file).read_text()) or {}
        for k, v in (data.get("steps") or {}).items():
            if k in specs and isinstance(v, str):
                specs[k] = v.strip()
    for step in STEPS:
        env_val = getattr(s, f"pipeline_{step}", "builtin")
        if env_val and env_val != "builtin":
            specs[step] = env_val.strip()
    return specs


def _load_class(path: str):
    mod, _, cls = path.partition(":")
    if not mod or not cls:
        raise ValueError(f"invalid plugin path '{path}' (use package.module:ClassName)")
    return getattr(importlib.import_module(mod), cls)


class PluginStep(Step):
    def __init__(self, step: str, spec: str):
        self.name = step
        self.impl = spec
        self.obj = _load_class(spec.removeprefix("python:"))()

    def run(self, inp: BaseModel) -> BaseModel:
        out_model = STEP_IO[self.name][1]
        result = self.obj.run(inp)
        return result if isinstance(result, out_model) else out_model.model_validate(
            result.model_dump() if isinstance(result, BaseModel) else result)


class HttpStep(Step):
    """Calls an external agent. Local files are exposed via signed, expiring URLs."""

    def __init__(self, step: str, url: str, client: httpx.Client | None = None):
        self.name = step
        self.impl = url
        s = get_settings()
        headers = {"user-agent": "reelchat-pipeline/1"}
        if s.pipeline_http_token:
            headers["authorization"] = f"Bearer {s.pipeline_http_token}"
        self.client = client or httpx.Client(timeout=s.pipeline_http_timeout_seconds, headers=headers)

    def run(self, inp: BaseModel) -> BaseModel:
        run_id = uuid.uuid4().hex
        work_dir = getattr(inp, "work_dir", None) or str(Path(get_settings().work_dir) / f"http-{run_id}")
        payload = {"step": self.name, "contract_version": CONTRACT_VERSION,
                   "input": _paths_to_urls(inp.model_dump(mode="json"), run_id)}
        try:
            r = self.client.post(self.impl, json=payload)
        except httpx.HTTPError as e:
            raise StepError(f"{self.name} agent unreachable: {e}", retryable=True, code="agent_unreachable") from e
        if r.status_code >= 500 or r.status_code == 429:
            raise StepError(f"{self.name} agent error {r.status_code}", retryable=True, code="agent_error")
        if r.status_code >= 400:
            raise StepError(f"{self.name} agent rejected input ({r.status_code}): {r.text[:200]}")
        try:
            data = _urls_to_paths(r.json(), Path(work_dir), self.client)
            return STEP_IO[self.name][1].model_validate(data)
        except (ValueError, ValidationError) as e:
            raise StepError(f"{self.name} agent returned an invalid response: {str(e)[:300]}") from e


def _paths_to_urls(obj: Any, run_id: str) -> Any:
    from app.security import sign_pipeline_file

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.endswith("_path") and isinstance(v, str):
                out[k[:-5] + "_url"] = sign_pipeline_file(v)
            elif k.endswith("_paths") and isinstance(v, list):
                out[k[:-6] + "_urls"] = [sign_pipeline_file(p) for p in v if isinstance(p, str)]
            elif k in ("work_dir",):
                continue
            else:
                out[k] = _paths_to_urls(v, run_id)
        return out
    if isinstance(obj, list):
        return [_paths_to_urls(v, run_id) for v in obj]
    return obj


def _urls_to_paths(obj: Any, work_dir: Path, client: httpx.Client) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.endswith("_url") and isinstance(v, str) and v.startswith(("http://", "https://")):
                out[k[:-4] + "_path"] = _download(v, work_dir, client)
            else:
                out[k] = _urls_to_paths(v, work_dir, client)
        return out
    if isinstance(obj, list):
        return [_urls_to_paths(v, work_dir, client) for v in obj]
    return obj


def _download(url: str, work_dir: Path, client: httpx.Client, limit: int = 500 * 1024 * 1024) -> str:
    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / f"agent-{uuid.uuid4().hex}{Path(httpx.URL(url).path).suffix[:8]}"
    size = 0
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise StepError("agent output file too large")
                fh.write(chunk)
    return str(dest)


def get_step(step: str, spec: str | None = None) -> Step:
    if step in _test_overrides:
        return _test_overrides[step]
    spec = spec or configured_specs()[step]
    if spec == "builtin":
        obj = _load_class(_BUILTINS[step])()
        obj.name, obj.impl = step, "builtin"
        return obj
    if spec.startswith("python:"):
        return PluginStep(step, spec)
    if spec.startswith(("http://", "https://")):
        return HttpStep(step, spec)
    raise ValueError(f"invalid pipeline spec for {step}: {spec!r}")


def run_step(step: str, inp: BaseModel, used: dict[str, str] | None = None) -> BaseModel:
    """Run a configured step; on failure of a custom agent optionally fall back to builtin."""
    impl = get_step(step)
    try:
        out = impl.run(inp)
        if used is not None:
            used[step] = impl.impl
        return out
    except Exception as e:  # noqa: BLE001
        if impl.impl == "builtin" or not get_settings().pipeline_fallback_to_builtin:
            raise
        log.warning("pipeline step %s via %s failed (%s); falling back to builtin", step, impl.impl, e)
        out = get_step(step, "builtin").run(inp)
        if used is not None:
            used[step] = f"builtin (fallback from {impl.impl})"
        return out


def describe() -> list[dict[str, str]]:
    specs = configured_specs()
    return [{"step": s, "implementation": specs[s]} for s in STEPS]
