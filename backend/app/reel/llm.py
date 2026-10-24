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

    @abstractmethod
    def analyze_images(self, prompt: str, images: list[tuple[str, str]], schema: dict) -> dict:
        """One call: prompt + labelled images -> JSON matching schema."""

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


def build_llm(provider: str | None = None, model: str | None = None) -> VisionLLM:
    provider = provider or os.environ.get("REEL_LLM_PROVIDER", "openai")
    if provider == "openai":
        return OpenAIVision(model=model)
    raise ValueError(f"unknown vision provider '{provider}'")
