"""Built-in script writer: the configured AI provider, falling back to playbook templates."""
from __future__ import annotations

import logging

from app import providers
from app.agents.rules import RulesAgentProvider
from app.pipeline.contracts import ScriptInput, ScriptOutput

log = logging.getLogger(__name__)


class ScriptWriter:
    def run(self, inp: ScriptInput) -> ScriptOutput:
        agent = providers.get_agent()
        if agent.is_llm:
            try:
                return agent.write_script(inp.items, inp.total_seconds, inp.context)
            except Exception as e:  # noqa: BLE001
                log.warning("script via %s failed (%s); using template", agent.name, e)
        return RulesAgentProvider().write_script(inp.items, inp.total_seconds, inp.context)
