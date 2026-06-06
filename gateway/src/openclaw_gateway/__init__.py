"""OpenClaw HA gateway brain.

Handles ``node.conversation.request`` frames from openclaw-hass-node by
routing the user turn to a premium model (Claude Opus 4.7 by default,
GPT-5.5 selectable) and emitting ``node.conversation.result`` when the
model produces a final response.

The brain owns model selection for the user-facing turn; subagents the
brain spawns for work are unpinned and chosen per task (see
``docs/RESEARCH-CONVERSATION-AGENT.md`` § Routing model).
"""

__version__ = "2026.6.0"
