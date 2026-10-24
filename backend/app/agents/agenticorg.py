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

log = logging.getLogger(__name__)


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
        self._client_options = {"api_key": api_key, "base_url": base_url, "timeout": timeout}
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
        inputs = {
            "statement": text,
            "context": {
                "state": ctx.state,
                "pending_group": ctx.pending_group,
                "preferences": ctx.prefs,
                "recent_messages": [
                    {"role": role, "text": message} for role, message in ctx.recent[-6:]
                ],
            },
        }
        kwargs: dict[str, Any] = {"action": self.action, "inputs": inputs}
        if self.company_id:
            kwargs["company_id"] = self.company_id
        if self.client is not None:
            result = self.client.agents.run(self.agent_name, **kwargs)
        else:
            # Providers are resolved per message; close each owned HTTP client.
            with self._client_factory(**self._client_options) as client:
                result = client.agents.run(self.agent_name, **kwargs)

        if result.status != "completed":
            raise AgentError("AgenticOrg run did not complete successfully")
        try:
            output = json.loads(result.output) if isinstance(result.output, str) else result.output
            # MessageAnalysis has defaults; require the interpretation envelope so
            # an empty/unrelated output triggers the original-message fallback.
            if not isinstance(output, dict) or not {"intent", "fields"} <= output.keys():
                raise ValueError("missing interpretation envelope")
            data = dict(output)
            data.setdefault("confidence", result.confidence)
            return MessageAnalysis.model_validate(data).sanitized()
        except (ValueError, TypeError, ValidationError) as exc:
            raise AgentError("AgenticOrg returned invalid message interpretation JSON") from exc

    def health(self) -> dict[str, Any]:
        # Configuration readiness only; do not run a billable agent health probe.
        return {"ok": True, "provider": self.name, "agent": self.agent_name,
                "action": self.action, "script_writer": "rules"}
