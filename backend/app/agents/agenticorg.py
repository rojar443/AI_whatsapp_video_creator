"""Interpret incoming messages with a configured AgenticOrg agent.

The remote agent returns MessageAnalysis JSON. The conversation engine consumes
that interpretation directly; it does not send the JSON through another LLM.
Script writing and replies retain the deterministic rules implementation.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.agents.base import AgentContext, AgentError, MessageAnalysis
from app.agents.rules import RulesAgentProvider
from app.conversation import preferences

log = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://agenticorg.hackathon.pinelabs.com"


class AgenticOrgAgentProvider(RulesAgentProvider):
    name = "agenticorg"
    is_llm = True

    def __init__(
        self, models=None, *, api_key: str | None = None,
        agent_name: str | None = None, base_url: str | None = None,
        company_id: str | None = None, action: str = "process",
        timeout: float = 30.0, client=None,
    ):
        super().__init__(models)
        if not agent_name:
            raise ValueError("AGENTICORG_AGENT_NAME is required for the agenticorg provider")
        if client is None and not api_key:
            raise ValueError("AGENTICORG_API_KEY is required for the agenticorg provider")
        self.agent_name = agent_name
        self.company_id = company_id
        self.action = action
        self.client = client
        self._client_options = {"api_key": api_key, "base_url": base_url or DEFAULT_BASE_URL, "timeout": timeout}
        self._client_factory = None
        if client is None:
            from agenticorg import AgenticOrg

            self._client_factory = AgenticOrg

    def analyze_message(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        try:
            return self._analyze_remote(text, ctx)
        except Exception as exc:  # noqa: BLE001 - optional interpretation must not block the user
            # Do not expose remote error bodies, credentials, or the user's text.
            log.info("AgenticOrg interpretation unavailable (%s); using original message", type(exc).__name__)
            return super().analyze_message(text, ctx).sanitized()

    def _analyze_remote(self, text: str, ctx: AgentContext) -> MessageAnalysis:
        kwargs: dict[str, Any] = {
            "action": self.action,
            "inputs": {"task": text},
            "context": {
                "state": ctx.state,
                "pending": ctx.pending,
                "media_summary": ctx.media_summary,
                "preferences": ctx.prefs,
                "recent_messages": [
                    {"role": role, "text": message} for role, message in ctx.recent[-6:]
                ],
            },
        }
        if self.company_id:
            kwargs["company_id"] = self.company_id
        if self.client is not None:
            result = self.client.agents.run(self.agent_name, **kwargs)
        else:
            # Providers are resolved per message; close each owned HTTP client.
            with self._client_factory(**self._client_options) as client:
                result = client.agents.run(self.agent_name, **kwargs)

        if result.status not in ("completed", "hitl_triggered"):
            raise AgentError("AgenticOrg run did not complete successfully")
        try:
            output = json.loads(result.output) if isinstance(result.output, str) else result.output
            if not isinstance(output, dict):
                raise ValueError("missing interpretation object")
            if not {"intent", "fields"} <= output.keys():
                return self._flat_interpretation(output, result.confidence, text, ctx)
            data = dict(output)
            data.setdefault("confidence", result.confidence)
            return MessageAnalysis.model_validate(data).sanitized()
        except (ValueError, TypeError, ValidationError) as exc:
            raise AgentError("AgenticOrg returned invalid message interpretation JSON") from exc

    def _flat_interpretation(
        self, output: dict[str, Any], confidence: float, text: str, ctx: AgentContext,
    ) -> MessageAnalysis:
        """Adapt the deployed agent's flat editing brief to local preferences."""
        aliases = {"duration_seconds", "additional_instructions", "pacing", "music_mood"}
        if not (set(preferences.ALL_FIELDS) | aliases) & output.keys():
            raise ValueError("missing editing preferences")
        original = super().analyze_message(text, ctx).sanitized()
        fields = dict(original.fields)
        fields.update({key: value for key, value in output.items() if key in preferences.ALL_FIELDS})
        instructions = [fields.get("instructions"), output.get("additional_instructions")]
        unsupported = list(original.unsupported)
        if output.get("duration_seconds") is not None:
            duration = preferences.coerce_field("duration", output["duration_seconds"])
            if duration is not None:
                fields["duration"] = duration
            else:
                unsupported.append(
                    f"Requested {output['duration_seconds']}-second duration; supported lengths are 15, 30, 60 seconds or original."
                )
        for key, label in (("pacing", "Pacing"), ("music_mood", "Music mood")):
            if output.get(key):
                instructions.append(f"{label}: {output[key]}")
        if output.get("aspect_ratio") not in (None, "9:16"):
            unsupported.append("Only the vertical 9:16 aspect ratio is supported.")
        combined = "; ".join(str(value) for value in instructions if value)
        if combined:
            fields["instructions"] = combined
        # ready_to_process describes the remote brief. It cannot approve a plan
        # or declare that the user has finished uploading media.
        return MessageAnalysis(
            intent=original.intent, confidence=confidence, fields=fields,
            unsupported=unsupported, done=original.done,
        ).sanitized()

    def health(self) -> dict[str, Any]:
        # Configuration readiness only; do not run a billable agent health probe.
        return {"ok": True, "provider": self.name, "agent": self.agent_name,
                "action": self.action, "script_writer": "rules"}
