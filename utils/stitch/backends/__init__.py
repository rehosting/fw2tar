"""Backend adapter layer for the stitcher harness.

Each Backend is responsible for one provider/transport (OpenAI native tools,
OpenAI JSON fallback, auto-detect, Anthropic, ...). The harness loop is
backend-agnostic: it builds an abstract conversation and asks the backend to
make a call.

OpenAI's `chat.completions` message shape is used as the canonical wire format
for `messages` because the existing backends are OpenAI-compat. Backends that
talk a different protocol (e.g. Anthropic's `messages` API with content
blocks) translate at the API boundary in their own `call()` method.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    """A normalized tool call surfaced by any backend. `id` is whatever the
    backend uses to correlate the tool result back; for backends that don't
    have native IDs (JSON fallback) the backend mints one.
    """
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class BackendResponse:
    """What a single `Backend.call()` returns. Normalized across backends."""
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    finish_reason: str = "stop"
    raw: Any = None  # the backend-native response object, for debugging


class Backend(Protocol):
    """Backend protocol. Methods take/return normalized types so the harness
    can stay backend-agnostic.
    """
    name: str

    def call(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        force_tool: str | None = None,
    ) -> BackendResponse:
        """Issue one round-trip to the model.

        - `system`: the system prompt (kept separate from `messages` because
          some providers want it as a top-level argument).
        - `messages`: OpenAI-chat-shape list — role + content (+ tool_calls /
          tool_call_id where appropriate). The OpenAI backends use this
          verbatim; other backends translate.
        - `tools`: OpenAI-style tools array (function name, description,
          parameters schema). Backends without native tool calling either
          translate this into a JSON-mode instruction or ignore it.
        - `force_tool`: name of a tool the model MUST call (used on the
          final-turn submit_plan force). None means "auto".
        """
        ...

    def assistant_turn(self, response: BackendResponse) -> dict:
        """Return the assistant message to append to `messages` after a call,
        so the next turn includes this turn's history. OpenAI-shape dict.
        """
        ...

    def tool_result_turns(
        self,
        tool_call: ToolCall,
        result_json: str,
    ) -> list[dict]:
        """Return the messages to append for a tool result. For OpenAI-native
        this is a single `role:tool` message; for JSON-fallback it's a
        synthetic user message; for other backends it could be a content
        block. Returns a list because some shapes need multiple turns.
        """
        ...


# --------- registry ---------

_BACKENDS: dict[str, type] = {}


def register(name: str):
    """Decorator: register a Backend class under a CLI-friendly name."""
    def deco(cls):
        cls.name = name
        _BACKENDS[name] = cls
        return cls
    return deco


def get_backend_class(name: str) -> type:
    if name not in _BACKENDS:
        raise SystemExit(
            f"unknown backend: {name!r}. Available: {sorted(_BACKENDS)}"
        )
    return _BACKENDS[name]


def available_backends() -> list[str]:
    return sorted(_BACKENDS)


# Import concrete backends so their @register decorators fire. Keep these at
# the bottom to avoid circular imports.
from . import openai_native  # noqa: E402, F401
from . import openai_json    # noqa: E402, F401
from . import openai_auto    # noqa: E402, F401
