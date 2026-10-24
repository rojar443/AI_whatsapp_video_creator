"""VisionLLM: the only place the pipeline talks to a model vendor.

Stages depend on this interface, so OpenAI can be swapped for another vendor, or for an
external orchestrator (e.g. AgenticOrg calling app.reel.tools over MCP), without touching them.
"""
from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    text: str  # JSON or plain text returned to the model
    images: list[tuple[str, str]] = field(default_factory=list)  # (label, image path)


ToolHandler = Callable[[str, dict], ToolResult]


class VisionLLMError(Exception):
    pass


class VisionLLM(ABC):
    name = "base"
    supports_video = False  # True when the model can watch video files and cite timestamps

    @abstractmethod
    def analyze_images(self, prompt: str, images: list[tuple[str, str]], schema: dict) -> dict:
        """One call: prompt + labelled images -> JSON matching schema."""

    def analyze_videos(self, prompt: str, videos: list[tuple[str, str]], images: list[tuple[str, str]],
                       schema: dict) -> dict:
        """One call: prompt + labelled video files (+ images) -> JSON matching schema.
        Only for supports_video models; callers fall back to frames otherwise."""
        raise NotImplementedError(f"{self.name} cannot take video input")

    @abstractmethod
    def run_tools(self, system: str, user: str, tools: list[dict], handler: ToolHandler,
                  max_steps: int = 8) -> str:
        """Tool-calling loop. tools are {name, description, parameters}. Returns final text."""


def _data_url(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/jpeg;base64," + base64.standard_b64encode(f.read()).decode()


class OpenAIVision(VisionLLM):
    """OpenAI Chat Completions with image inputs, strict JSON schemas and function calling."""

    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None,
                 timeout: float = 120.0, client: Any = None, detail: str = "low"):
        self.model = model or os.environ.get("REEL_LLM_MODEL", "gpt-5-mini")
        self.detail = detail
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"), base_url=base_url,
                            timeout=timeout)
        self.client = client

    def _image_parts(self, images: list[tuple[str, str]]) -> list[dict]:
        parts: list[dict] = []
        for label, path in images:
            parts.append({"type": "text", "text": label})
            parts.append({"type": "image_url", "image_url": {"url": _data_url(path), "detail": self.detail}})
        return parts

    def analyze_images(self, prompt, images, schema):
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt},
                                                   *self._image_parts(images)]}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "result", "schema": schema, "strict": True}},
        )
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise VisionLLMError(f"model refused: {msg.refusal}")
        try:
            return json.loads(msg.content or "{}")
        except json.JSONDecodeError as e:
            raise VisionLLMError("model returned invalid JSON") from e

    def run_tools(self, system, user, tools, handler, max_steps=8):
        specs = [{"type": "function", "function": t} for t in tools]
        messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for _ in range(max_steps):
            resp = self.client.chat.completions.create(model=self.model, messages=messages, tools=specs)
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            if not calls:
                return msg.content or ""
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.function.name,
                                                          "arguments": c.function.arguments}}
                                            for c in calls]})
            images: list[tuple[str, str]] = []
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                result = (ToolResult(json.dumps({"error": "arguments were not valid JSON"}))
                          if args is None else handler(c.function.name, args))
                messages.append({"role": "tool", "tool_call_id": c.id, "content": result.text})
                images += result.images
            if images:  # tool messages are text-only, so images follow as a user turn
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": "Images returned by your tool calls:"},
                    *self._image_parts(images)]})
        raise VisionLLMError(f"no final answer after {max_steps} steps")


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise VisionLLMError("model returned invalid JSON") from e


class GeminiVision(VisionLLM):
    """Google Gemini: watches uploaded video files natively and cites timestamps.
    Same schema-in-prompt + JSON response approach as app.agents.llm.GeminiAgentProvider."""

    name = "gemini"
    supports_video = True

    def __init__(self, model: str | None = None, api_key: str | None = None, client: Any = None,
                 upload_timeout: float = 180.0):
        self.model = model or os.environ.get("REEL_LLM_MODEL", "gemini-2.5-flash")
        self.upload_timeout = upload_timeout
        if client is None:
            from google import genai

            api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY is required for the gemini vision provider")
            client = genai.Client(api_key=api_key)
        self.client = client

    def _generate(self, parts: list, prompt: str, schema: dict) -> dict:
        from google.genai import types

        system = prompt + "\n\nRespond with ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema)
        resp = self.client.models.generate_content(
            model=self.model, contents=parts,
            config=types.GenerateContentConfig(system_instruction=system,
                                               response_mime_type="application/json"))
        return _parse_json(resp.text)

    def _image_parts(self, images: list[tuple[str, str]]) -> list:
        from google.genai import types

        parts: list = []
        for label, path in images:
            with open(path, "rb") as f:
                parts += [label, types.Part.from_bytes(data=f.read(), mime_type="image/jpeg")]
        return parts

    def _upload(self, path: str):
        import time

        f = self.client.files.upload(file=path)
        deadline = time.monotonic() + self.upload_timeout
        while getattr(f.state, "name", str(f.state)) == "PROCESSING":
            if time.monotonic() > deadline:
                raise VisionLLMError(f"upload of {os.path.basename(path)} did not finish processing")
            time.sleep(2)
            f = self.client.files.get(name=f.name)
        if getattr(f.state, "name", str(f.state)) == "FAILED":
            raise VisionLLMError(f"Gemini could not process {os.path.basename(path)}")
        return f

    def analyze_images(self, prompt, images, schema):
        return self._generate(["Analyse these images.", *self._image_parts(images)], prompt, schema)

    def analyze_videos(self, prompt, videos, images, schema):
        uploaded = []
        try:
            parts: list = []
            for label, path in videos:
                f = self._upload(path)
                uploaded.append(f)
                parts += [label, f]
            parts += self._image_parts(images)
            return self._generate(parts, prompt, schema)
        finally:
            for f in uploaded:  # uploads are temporary; do not leave user footage on Google's side
                try:
                    self.client.files.delete(name=f.name)
                except Exception:  # noqa: BLE001 - cleanup is best effort
                    pass

    def run_tools(self, system, user, tools, handler, max_steps=8):
        # Not needed for the default (timeline) selector; DirectorSelector falls back to the heuristic.
        raise VisionLLMError("tool calling is not implemented for the gemini vision provider")


def build_llm(provider: str | None = None, model: str | None = None) -> VisionLLM:
    provider = provider or os.environ.get("REEL_LLM_PROVIDER", "gemini")
    if provider == "gemini":
        return GeminiVision(model=model)
    if provider == "openai":
        return OpenAIVision(model=model)
    raise ValueError(f"unknown vision provider '{provider}'")
