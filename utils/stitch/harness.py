"""Tool-use loop driving a local LLM through firmware-fragment analysis.

Two modes:
  * Native: OpenAI-style tool calling (most servers — vllm, recent ollama, etc.)
  * Fallback: model emits one JSON object per turn — for servers that don't
    support tools.

Auto-detect: try native first, fall back if the server 400s on `tools=` or
returns two empty `tool_calls` in a row.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .plan import StitchPlan
from .prompts import (
    FALLBACK_SYSTEM_PROMPT,
    INITIAL_USER_PROMPT,
    NUDGE_FORCE_SUBMIT,
    NUDGE_NO_TOOL,
    NUDGE_VALIDATION,
    SYSTEM_PROMPT,
)
from .tools import (
    TOOLS,
    TOOLS_BY_NAME,
    FragmentCache,
    FragmentOnlyArgs,
    tool_fs_summary,
    to_openai_schemas,
)


def _import_openai():
    try:
        from openai import OpenAI  # type: ignore
        return OpenAI
    except ImportError as e:
        raise SystemExit(
            "openai package not installed. "
            "Install with: pip install -r fw2tar/utils/stitch/requirements.txt"
        ) from e


@dataclass
class HarnessConfig:
    base_url: str | None
    api_key: str
    model: str
    max_turns: int = 10
    request_timeout: float = 120.0
    force_fallback: bool = False
    verbose: bool = False
    insecure: bool = False  # skip TLS cert verification (self-signed local models)


def _make_client(OpenAI, cfg: "HarnessConfig"):
    """Construct the OpenAI client, honoring cfg.insecure for self-signed servers."""
    if cfg.insecure:
        try:
            import httpx  # openai SDK already depends on httpx
        except ImportError as e:
            raise SystemExit(
                "--insecure requires httpx (a transitive dep of openai). Reinstall openai."
            ) from e
        http_client = httpx.Client(verify=False, timeout=cfg.request_timeout)
        return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, http_client=http_client)
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.request_timeout)


@dataclass
class TurnLog:
    role: str
    content: str
    tool_name: str | None = None


@dataclass
class RunResult:
    plan: StitchPlan
    used_fallback: bool
    turns: int
    transcript: list[TurnLog] = field(default_factory=list)


def _fragment_summary_block(cache: FragmentCache) -> str:
    """Build the precomputed fs_summary block injected into the initial user
    message. Saves the LLM from spending its first N turns calling fs_summary.
    """
    chunks = []
    for name in cache.names():
        s = tool_fs_summary(cache, FragmentOnlyArgs(fragment=name))
        provenance_parts = []
        if "fs_type_guess" in s:
            provenance_parts.append(f"fs_type_guess={s['fs_type_guess']}")
        if "root_path" in s:
            provenance_parts.append(f"unblob_path={s['root_path']!r}")
        if "shard_score" in s:
            provenance_parts.append(f"score={s['shard_score']}")
        provenance = ("\n    " + ", ".join(provenance_parts)) if provenance_parts else ""
        chunks.append(
            f"- {name}\n"
            f"    extractor={s['extractor']}, index={s['index']}, "
            f"size={s['compressed_size']} bytes" + provenance + "\n"
            f"    has_etc_passwd={s['has_etc_passwd']}, has_sbin_init={s['has_sbin_init']}, "
            f"has_bin_sh={s['has_bin_sh']}, has_lib_ld={s['has_lib_ld']}, "
            f"has_etc_fstab={s['has_etc_fstab']}, has_etc_inittab={s['has_etc_inittab']}, "
            f"has_etc_init_d_rcS={s['has_etc_init_d_rcS']}\n"
            f"    top_dirs={[(d['name'], d['count']) for d in s['top_dirs']]}"
        )
    return "\n".join(chunks)


# --------------- Reachability ---------------

def reachability_check(client, model: str) -> None:
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            timeout=10.0,
        )
    except Exception as e:
        raise SystemExit(f"LLM endpoint unreachable (model={model!r}): {e}")


# --------------- Native tool-use loop ---------------

def _run_native(cache: FragmentCache, cfg: HarnessConfig, OpenAI) -> RunResult:
    client = _make_client(OpenAI, cfg)
    reachability_check(client, cfg.model)

    plan_schema = StitchPlan.model_json_schema()
    tool_schemas = to_openai_schemas(plan_schema)

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": INITIAL_USER_PROMPT.format(
                fragment_summaries=_fragment_summary_block(cache)
            ),
        },
    ]
    transcript: list[TurnLog] = [TurnLog(role="system", content=SYSTEM_PROMPT),
                                  TurnLog(role="user", content=messages[1]["content"])]

    consecutive_empty = 0
    same_tool_validation_fail: dict[str, int] = {}

    for turn in range(cfg.max_turns):
        force_submit = (turn == cfg.max_turns - 1)
        if force_submit:
            messages.append({"role": "user", "content": NUDGE_FORCE_SUBMIT})
            transcript.append(TurnLog(role="user", content=NUDGE_FORCE_SUBMIT))

        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "tools": tool_schemas,
            "temperature": 0.0,
        }
        if force_submit:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": "submit_plan"}}
        else:
            kwargs["tool_choice"] = "auto"

        if cfg.verbose:
            print(f"[harness] turn {turn+1}/{cfg.max_turns} (native)", file=sys.stderr)

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            err_msg = str(e)
            # Crude heuristic: server rejected tools — escalate to fallback.
            if "tool" in err_msg.lower() and ("not support" in err_msg.lower() or "400" in err_msg):
                raise _SwitchToFallback(err_msg)
            raise

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        text = msg.content or ""

        transcript.append(TurnLog(
            role="assistant",
            content=text or json.dumps([
                {"name": tc.function.name, "args": tc.function.arguments} for tc in tool_calls
            ]),
        ))

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        if not tool_calls:
            consecutive_empty += 1
            if consecutive_empty >= 2 and not force_submit:
                # Server probably doesn't support tools; escalate.
                raise _SwitchToFallback("two consecutive empty tool_calls")
            messages.append({"role": "user", "content": NUDGE_NO_TOOL})
            transcript.append(TurnLog(role="user", content=NUDGE_NO_TOOL))
            continue
        consecutive_empty = 0

        # Handle each tool call sequentially.
        terminated_plan: StitchPlan | None = None
        for tc in tool_calls:
            name = tc.function.name
            try:
                raw_args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "name": name,
                    "content": json.dumps({"error": f"bad json arguments: {e}"}),
                })
                continue

            if name == "submit_plan":
                try:
                    plan = StitchPlan.model_validate(raw_args)
                    terminated_plan = plan
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": name,
                        "content": json.dumps({"ok": True}),
                    })
                except ValidationError as e:
                    err = {"error": "plan failed validation", "details": e.errors()[:5]}
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": name,
                        "content": json.dumps(err),
                    })
                continue

            tool = TOOLS_BY_NAME.get(name)
            if tool is None:
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "name": name,
                    "content": json.dumps({"error": f"unknown tool: {name}"}),
                })
                continue

            try:
                args_obj = tool.args_model.model_validate(raw_args)
            except ValidationError as e:
                same_tool_validation_fail[name] = same_tool_validation_fail.get(name, 0) + 1
                if same_tool_validation_fail[name] > 2:
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": name,
                        "content": json.dumps({"error": "too many validation failures for this tool, stop using it"}),
                    })
                    continue
                schema = tool.args_model.model_json_schema()
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "name": name,
                    "content": NUDGE_VALIDATION.format(error=str(e), schema=json.dumps(schema)[:1500]),
                })
                continue

            try:
                result = tool.fn(cache, args_obj)
            except Exception as e:
                result = {"error": f"tool raised: {e}"}
            messages.append({
                "role": "tool", "tool_call_id": tc.id, "name": name,
                "content": json.dumps(result)[:8000],
            })
            transcript.append(TurnLog(role="tool", content=json.dumps(result)[:1000], tool_name=name))

        if terminated_plan is not None:
            return RunResult(plan=terminated_plan, used_fallback=False,
                              turns=turn + 1, transcript=transcript)

    raise SystemExit("loop terminated without a valid plan (max_turns reached)")


# --------------- Fallback (JSON-mode) loop ---------------

class _SwitchToFallback(Exception):
    pass


def _extract_first_json_object(text: str) -> dict | None:
    """Extract the first balanced {...} from text. Used for fallback parsing.

    Re's nested group recursion isn't available in stdlib; we do a manual scan.
    """
    s = text
    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            c = s[i]
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
                        candidate = s[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
        start = s.find("{", start + 1)
    return None


def _tool_descriptions_block() -> str:
    parts = []
    for t in TOOLS:
        parts.append(f"  - {t.name}: {t.description}\n      args schema: {json.dumps(t.args_model.model_json_schema())}")
    return "\n".join(parts)


def _run_fallback(cache: FragmentCache, cfg: HarnessConfig, OpenAI) -> RunResult:
    client = _make_client(OpenAI, cfg)
    reachability_check(client, cfg.model)

    plan_schema = StitchPlan.model_json_schema()
    system = FALLBACK_SYSTEM_PROMPT.format(
        tool_descriptions=_tool_descriptions_block(),
        plan_schema=json.dumps(plan_schema),
    )
    messages: list[dict] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": INITIAL_USER_PROMPT.format(
                fragment_summaries=_fragment_summary_block(cache)
            ),
        },
    ]
    transcript: list[TurnLog] = [TurnLog(role="system", content="<fallback system prompt>"),
                                  TurnLog(role="user", content=messages[1]["content"])]

    consecutive_unparsed = 0
    consecutive_validation_fail = 0

    for turn in range(cfg.max_turns):
        force_submit = (turn == cfg.max_turns - 1)
        if force_submit:
            messages.append({"role": "user", "content": NUDGE_FORCE_SUBMIT})
            transcript.append(TurnLog(role="user", content=NUDGE_FORCE_SUBMIT))

        if cfg.verbose:
            print(f"[harness] turn {turn+1}/{cfg.max_turns} (fallback)", file=sys.stderr)

        resp = client.chat.completions.create(
            model=cfg.model, messages=messages, temperature=0.0,
        )
        text = resp.choices[0].message.content or ""
        transcript.append(TurnLog(role="assistant", content=text))
        messages.append({"role": "assistant", "content": text})

        obj = _extract_first_json_object(text)
        if obj is None:
            consecutive_unparsed += 1
            if consecutive_unparsed >= 2:
                raise SystemExit("fallback: model produced no parseable JSON for 2 turns")
            messages.append({"role": "user", "content": "Your last message did not contain a parseable JSON object. Respond with exactly one JSON object."})
            continue
        consecutive_unparsed = 0

        if "final" in obj:
            try:
                plan = StitchPlan.model_validate(obj["final"])
                return RunResult(plan=plan, used_fallback=True,
                                  turns=turn + 1, transcript=transcript)
            except ValidationError as e:
                consecutive_validation_fail += 1
                if consecutive_validation_fail >= 2:
                    raise SystemExit(f"fallback: plan failed validation twice: {e}")
                messages.append({"role": "user", "content": f"Your plan failed validation: {e}. Fix and resend."})
                continue

        if "tool" in obj:
            name = obj.get("tool")
            args = obj.get("args") or {}
            tool = TOOLS_BY_NAME.get(name)
            if tool is None:
                messages.append({"role": "user", "content": json.dumps({"error": f"unknown tool: {name}"})})
                continue
            try:
                args_obj = tool.args_model.model_validate(args)
            except ValidationError as e:
                messages.append({"role": "user", "content": json.dumps({"error": "bad args", "details": str(e)})})
                continue
            try:
                result = tool.fn(cache, args_obj)
            except Exception as e:
                result = {"error": f"tool raised: {e}"}
            messages.append({"role": "user", "content": json.dumps({"tool_result": {"name": name, "result": result}})[:8000]})
            transcript.append(TurnLog(role="tool", content=json.dumps(result)[:1000], tool_name=name))
            continue

        messages.append({"role": "user", "content": "JSON did not contain 'tool' or 'final' — re-read instructions and try again."})

    raise SystemExit("fallback loop terminated without a valid plan (max_turns reached)")


# --------------- Entry point ---------------

def run(frag_dir: Path, cfg: HarnessConfig) -> RunResult:
    OpenAI = _import_openai()
    cache = FragmentCache(frag_dir)
    if not cache.names():
        raise SystemExit(
            f"no fragment .tar.gz files found in {frag_dir}. "
            "Run `fwstitch shard <firmware> -o <dir>` first to produce shards."
        )
    if len(cache.names()) == 1:
        print(
            f"WARNING: only one fragment in {frag_dir}: {cache.names()[0]}. "
            "Stitching may be a no-op. Try `fwstitch shard --min-score 3` (lower threshold) "
            "or `fwstitch shard --extractor binwalk` to capture more fragments.",
            file=sys.stderr,
        )

    try:
        if cfg.force_fallback:
            return _run_fallback(cache, cfg, OpenAI)
        try:
            return _run_native(cache, cfg, OpenAI)
        except _SwitchToFallback as e:
            if cfg.verbose:
                print(f"[harness] switching to fallback: {e}", file=sys.stderr)
            return _run_fallback(cache, cfg, OpenAI)
    finally:
        cache.close()
