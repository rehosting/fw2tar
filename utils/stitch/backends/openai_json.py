"""OpenAI-compat backend using JSON-emission instead of native tool calling.

For models or servers that don't support native tool/function calling well —
gpt-3.5-turbo, older local llama.cpp builds, smaller Gemma/Qwen variants on
ollama, etc. The model is asked to emit a single JSON object per turn:

    {"tool": "<name>", "args": { ... }}    # to call a tool
    {"final": { ...StitchPlan... }}        # to terminate the loop

The robustness layer here exists because real models are slop:
  * They wrap JSON in ```json fences or extra prose
  * They emit `True`/`False`/`None` (Python) instead of `true`/`false`/`null`
  * They use single quotes
  * They append a trailing comma
  * They mistype tool names with hyphens-vs-underscores or wrong case
  * They sometimes emit two JSON objects in one response
  * They sometimes call the same tool with the same args three turns in a row

All of these are recovered locally rather than blowing up to the harness.
"""
from __future__ import annotations

import itertools
import json
import re
import uuid
from typing import Any, Optional

from . import BackendResponse, ToolCall, register
from .openai_native import _build_client


# Names of tools registered in tools.py. Filled in by harness at startup so
# the backend can fuzzy-match misspelled names back to canonical ones.
_VALID_TOOL_NAMES: set[str] = set()


def set_valid_tool_names(names: set[str]) -> None:
    """Called by the harness once it knows the registered tool set."""
    _VALID_TOOL_NAMES.clear()
    _VALID_TOOL_NAMES.update(names)


# ---------------- JSON extraction & repair ----------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")


def _strip_fences(text: str) -> str:
    """Pull JSON out of ```json ... ``` or ``` ... ``` fences if present.
    If multiple fences, return the first one. If none, return the text
    unchanged so the brace-scanner can try its luck.
    """
    m = _FENCE_RE.search(text)
    return m.group(1) if m else text


def _find_balanced_json_objects(text: str) -> list[str]:
    """Return every balanced top-level {...} substring in `text`. Skips
    quoted strings (with backslash escapes) so braces inside string literals
    don't confuse the depth counter.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[start:j + 1])
                        break
            j += 1
        i = j + 1
    return out


def _repair_python_literals(blob: str) -> str:
    """Rewrite Python-flavored literals into JSON. Conservative: only touches
    bare-word tokens to avoid corrupting string contents.
    """
    blob = re.sub(r"\bTrue\b", "true", blob)
    blob = re.sub(r"\bFalse\b", "false", blob)
    blob = re.sub(r"\bNone\b", "null", blob)
    return blob


def _strip_trailing_commas(blob: str) -> str:
    """Remove `,}` and `,]` patterns that some models emit."""
    return re.sub(r",(\s*[}\]])", r"\1", blob)


def _try_parse(blob: str) -> Optional[dict]:
    """Try increasingly aggressive repairs to coax a JSON object out of `blob`."""
    attempts = [
        blob,
        _strip_trailing_commas(blob),
        _repair_python_literals(blob),
        _repair_python_literals(_strip_trailing_commas(blob)),
    ]
    for a in attempts:
        try:
            obj = json.loads(a)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def extract_json_object(text: str) -> Optional[dict]:
    """Find the first parseable {...} in `text`. Tries fence-stripped first,
    then raw text. Returns None if nothing usable is found.
    """
    for source in (_strip_fences(text), text):
        for candidate in _find_balanced_json_objects(source):
            obj = _try_parse(candidate)
            if obj is not None:
                return obj
    return None


# ---------------- Tool-name fuzzy match ----------------

def _normalize_tool_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def fuzzy_match_tool_name(name: str) -> Optional[str]:
    """Map a model-emitted name to a registered tool name, tolerating case
    and dash/underscore/space differences. None if no unique match.
    """
    if not _VALID_TOOL_NAMES:
        return name
    if name in _VALID_TOOL_NAMES:
        return name
    target = _normalize_tool_name(name)
    matches = [n for n in _VALID_TOOL_NAMES if _normalize_tool_name(n) == target]
    if len(matches) == 1:
        return matches[0]
    return None


# ---------------- The backend ----------------

_JSON_INSTRUCTIONS = """\

IMPORTANT: This server does NOT support native tool calling. On every turn,
respond with a SINGLE JSON object on its own (no surrounding prose, no
markdown fences) in exactly one of these two forms:

  Tool call:  {{"tool": "<name>", "args": {{...args...}}}}
  Final:      {{"final": {{...StitchPlan...}}}}

Available tools (call these by name in the "tool" field):

{tool_descriptions}

StitchPlan schema for the final response:

{plan_schema}

If you emit anything other than a single JSON object, the harness cannot
parse your response and will nudge you to try again.
"""


def _tool_descriptions_block(tools: list[dict]) -> str:
    lines = []
    for t in tools:
        f = t.get("function", {})
        lines.append(f"  - {f.get('name')}: {f.get('description','')}")
        params = f.get("parameters", {})
        lines.append(f"      args schema: {json.dumps(params)}")
    return "\n".join(lines)


@register("openai-json")
class OpenAIJSONBackend:
    name = "openai-json"

    def __init__(self, cfg):
        self.cfg = cfg
        self.client = _build_client(cfg)
        self._id_counter = itertools.count(1)

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
        # Build a plan_schema entry for the instructions, lifting it out of
        # the submit_plan tool definition.
        plan_schema = ""
        for t in tools:
            if t.get("function", {}).get("name") == "submit_plan":
                plan_schema = json.dumps(t["function"].get("parameters", {}))
                break

        # Filter the tool list to non-terminal tools for the description block.
        non_terminal = [t for t in tools if t.get("function", {}).get("name") != "submit_plan"]
        full_system = system + _JSON_INSTRUCTIONS.format(
            tool_descriptions=_tool_descriptions_block(non_terminal),
            plan_schema=plan_schema,
        )
        if force_tool is not None:
            full_system += (
                f"\n\nThis is the final turn. You MUST respond with "
                + ('a {"final": {...StitchPlan...}} object now.' if force_tool == "submit_plan"
                   else f'a tool call to {force_tool!r} now.')
            )

        msgs = [{"role": "system", "content": full_system}, *messages]

        resp = self.client.chat.completions.create(
            model=self.cfg.model,
            messages=msgs,
            temperature=0.0,
        )
        text = resp.choices[0].message.content or ""
        finish = resp.choices[0].finish_reason or "stop"

        # Parse with the robustness layer.
        obj = extract_json_object(text)
        tool_calls: list[ToolCall] = []
        if obj is not None:
            if "final" in obj:
                tool_calls = [ToolCall(
                    id=f"json-{next(self._id_counter)}",
                    name="submit_plan",
                    args=obj["final"] if isinstance(obj["final"], dict) else {},
                )]
            elif "tool" in obj:
                requested = obj.get("tool", "")
                canonical = fuzzy_match_tool_name(requested) or requested
                args = obj.get("args", {})
                if not isinstance(args, dict):
                    args = {}
                tool_calls = [ToolCall(
                    id=f"json-{next(self._id_counter)}",
                    name=canonical,
                    args=args,
                )]

        return BackendResponse(
            tool_calls=tool_calls,
            text=text,
            finish_reason=finish,
            raw=resp,
        )

    def assistant_turn(self, response: BackendResponse) -> dict:
        # Echo the model's literal text back into history so it sees its own
        # prior turns. We don't reformat the tool call as JSON — the model's
        # actual output is what shows up in context.
        return {"role": "assistant", "content": response.text}

    def tool_result_turns(self, tool_call: ToolCall, result_json: str) -> list[dict]:
        return [{
            "role": "user",
            "content": json.dumps({
                "tool_result": {
                    "name": tool_call.name,
                    "result": _truncate(result_json, 6000),
                }
            }),
        }]


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated {len(s)-limit} chars]"
