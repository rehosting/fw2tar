"""OpenAI-compat backend using native tool/function calling.

Works against api.openai.com, vllm, recent ollama, llama.cpp server, etc. —
anything that implements the OpenAI `chat.completions` endpoint with the
`tools` parameter.
"""
from __future__ import annotations

import json
from typing import Any

from . import BackendResponse, ToolCall, register


def _import_openai():
    try:
        from openai import OpenAI  # type: ignore
        return OpenAI
    except ImportError as e:
        raise SystemExit(
            "openai package not installed. "
            "Install with: pip install -r fw2tar/utils/stitch/requirements.txt"
        ) from e


def _build_client(cfg):
    OpenAI = _import_openai()
    if cfg.insecure:
        try:
            import httpx
        except ImportError as e:
            raise SystemExit(
                "--insecure requires httpx (a transitive dep of openai)."
            ) from e
        http_client = httpx.Client(verify=False, timeout=cfg.request_timeout)
        return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, http_client=http_client)
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.request_timeout)


@register("openai-native")
class OpenAINativeBackend:
    name = "openai-native"

    def __init__(self, cfg):
        self.cfg = cfg
        self.client = _build_client(cfg)

    def reachability_check(self) -> None:
        try:
            self.client.chat.completions.create(
                model=self.cfg.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                timeout=10.0,
            )
        except Exception as e:
            raise SystemExit(f"LLM endpoint unreachable (model={self.cfg.model!r}): {e}")

    def call(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        force_tool: str | None = None,
    ) -> BackendResponse:
        # OpenAI wants system as the first message.
        msgs = [{"role": "system", "content": system}, *messages]
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": msgs,
            "tools": tools,
            "temperature": 0.0,
        }
        if force_tool is not None:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        else:
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        raw_tool_calls = getattr(msg, "tool_calls", None) or []
        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"__raw_arguments__": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))

        return BackendResponse(
            tool_calls=tool_calls,
            text=msg.content or "",
            finish_reason=resp.choices[0].finish_reason or "stop",
            raw=resp,
        )

    def assistant_turn(self, response: BackendResponse) -> dict:
        m: dict[str, Any] = {"role": "assistant", "content": response.text}
        if response.tool_calls:
            m["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.args)},
                }
                for tc in response.tool_calls
            ]
        return m

    def tool_result_turns(self, tool_call: ToolCall, result_json: str) -> list[dict]:
        return [{
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": tool_call.name,
            "content": result_json,
        }]
