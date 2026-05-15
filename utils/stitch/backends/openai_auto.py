"""Auto-detecting OpenAI-compat backend.

Starts in native tool-calling mode. If the server rejects the `tools`
parameter with a 4xx, or the model returns two consecutive empty `tool_calls`
responses, transparently switches to JSON-fallback mode and replays the
conversation. After switching, behaves identically to OpenAIJSONBackend for
the rest of the run.

This is the default backend — it's the right answer for "I have an
OpenAI-compatible endpoint, I don't know whether it does tool calling well."
"""
from __future__ import annotations

import sys
from typing import Optional

from . import Backend, BackendResponse, ToolCall, register
from .openai_native import OpenAINativeBackend
from .openai_json import OpenAIJSONBackend


@register("openai-auto")
class OpenAIAutoBackend:
    name = "openai-auto"

    def __init__(self, cfg):
        self.cfg = cfg
        self._native = OpenAINativeBackend(cfg)
        self._json: Optional[OpenAIJSONBackend] = None
        self._using_json = False
        self._consecutive_empty = 0

    def reachability_check(self) -> None:
        self._native.reachability_check()

    def _ensure_json(self) -> OpenAIJSONBackend:
        if self._json is None:
            self._json = OpenAIJSONBackend(self.cfg)
        return self._json

    def _switch_to_json(self, reason: str) -> None:
        if not self._using_json:
            if self.cfg.verbose:
                print(f"[backend] switching to openai-json mode: {reason}", file=sys.stderr)
            self._using_json = True

    def _active(self) -> Backend:
        return self._ensure_json() if self._using_json else self._native

    def call(self, system, messages, tools, force_tool=None) -> BackendResponse:
        if self._using_json:
            return self._json.call(system, messages, tools, force_tool=force_tool)

        try:
            resp = self._native.call(system, messages, tools, force_tool=force_tool)
        except Exception as e:
            msg = str(e).lower()
            if any(s in msg for s in ("tool", "function")) and ("not support" in msg or "400" in msg or "unsupported" in msg):
                self._switch_to_json(f"server rejected tools: {e!s:.200}")
                return self._ensure_json().call(system, messages, tools, force_tool=force_tool)
            raise

        if not resp.tool_calls:
            self._consecutive_empty += 1
            if self._consecutive_empty >= 2 and force_tool is None:
                self._switch_to_json("two consecutive empty tool_calls")
                return self._ensure_json().call(system, messages, tools, force_tool=force_tool)
        else:
            self._consecutive_empty = 0
        return resp

    def assistant_turn(self, response: BackendResponse) -> dict:
        return self._active().assistant_turn(response)

    def tool_result_turns(self, tool_call: ToolCall, result_json: str) -> list[dict]:
        return self._active().tool_result_turns(tool_call, result_json)
