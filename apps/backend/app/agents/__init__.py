"""Agent layer (PR #20 / Sprint 3+).

docs/04-architecture.md §16 + docs/plans/phase1-sprint3-plan.md
§"Audit Log writer + ``BaseAgent`` skeleton". Every agent is a
subclass of :class:`BaseAgent` and is constructed via the factory
:func:`build_agent`. The only "real" agent in this PR is
:class:`HealthCheckAgent` — the rest land in Sprints 4–8.
"""

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.healthcheck import HealthCheckAgent

__all__ = [
    "AgentContext",
    "AgentResult",
    "BaseAgent",
    "HealthCheckAgent",
]
