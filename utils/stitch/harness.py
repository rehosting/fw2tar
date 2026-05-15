"""Tool-use loop driving an LLM through firmware-fragment analysis.

Backend-agnostic — talks to whatever `Backend` instance you pass in (see
`backends/`). The loop's job is:
  * build the system prompt + initial user message from the fragment cache
  * call the backend each turn, dispatch tool calls, validate, append history
  * detect pathological states (stuck-on-same-tool, narrating without calling)
  * force `submit_plan` on the final turn
  * return the validated StitchPlan
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .backends import Backend, BackendResponse, ToolCall, get_backend_class
from .backends.openai_json import set_valid_tool_names
from .plan import StitchPlan
from .prompts import (
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


@dataclass
class HarnessConfig:
    base_url: str | None
    api_key: str
    model: str
    max_turns: int = 15
    request_timeout: float = 120.0
    backend: str = "openai-auto"  # CLI string, resolved to a Backend class
    verbose: bool = False
    insecure: bool = False
    debug_transcript: Path | None = None


@dataclass
class TurnLog:
    role: str
    content: str
    tool_name: str | None = None


@dataclass
class RunResult:
    plan: StitchPlan
    backend_name: str
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


def _minimal_repair_example(args_model_cls) -> str:
    """Tiny example payload for the args model, used in validation-error
    nudges. Far more useful for a weak model than dumping the full schema.
    """
    fields = args_model_cls.model_fields
    example: dict[str, Any] = {}
    for fname, finfo in fields.items():
        if finfo.is_required():
            # Cheap stub by annotation type name
            ann = repr(finfo.annotation)
            if "int" in ann:
                example[fname] = 10
            elif "bool" in ann:
                example[fname] = True
            elif "list" in ann.lower():
                example[fname] = []
            elif "dict" in ann.lower():
                example[fname] = {}
            else:
                example[fname] = "..."
    return json.dumps(example)


def _tool_call_fingerprint(tc: ToolCall) -> str:
    """Stable string key for stuck-detection."""
    try:
        return f"{tc.name}({json.dumps(tc.args, sort_keys=True)})"
    except (TypeError, ValueError):
        return f"{tc.name}(<unhashable>)"


def _write_debug(path: Path, role: str, content: str, tool_name: str | None = None) -> None:
    with open(path, "a", encoding="utf-8") as f:
        prefix = f"--- {role}"
        if tool_name:
            prefix += f" [{tool_name}]"
        prefix += " ---\n"
        f.write(prefix)
        f.write(content if isinstance(content, str) else json.dumps(content))
        f.write("\n")


def run(frag_dir: Path, cfg: HarnessConfig) -> RunResult:
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

    # Tell the JSON backend what tool names exist so it can fuzzy-match.
    set_valid_tool_names({t.name for t in TOOLS} | {"submit_plan"})

    backend_cls = get_backend_class(cfg.backend)
    backend: Backend = backend_cls(cfg)
    backend.reachability_check()

    plan_schema = StitchPlan.model_json_schema()
    tool_schemas = to_openai_schemas(plan_schema)

    # Build the conversation. We keep just `messages` (post-system); the
    # backend injects the system prompt at call time.
    initial_user = INITIAL_USER_PROMPT.format(
        fragment_summaries=_fragment_summary_block(cache),
    )
    messages: list[dict] = [{"role": "user", "content": initial_user}]
    transcript: list[TurnLog] = [
        TurnLog(role="system", content=SYSTEM_PROMPT),
        TurnLog(role="user", content=initial_user),
    ]
    if cfg.debug_transcript:
        cfg.debug_transcript.write_text("")  # truncate
        _write_debug(cfg.debug_transcript, "system", SYSTEM_PROMPT)
        _write_debug(cfg.debug_transcript, "user", initial_user)

    recent_calls: list[str] = []
    consecutive_no_tool = 0
    same_tool_fail: dict[str, int] = {}
    submission_attempted = False  # did the model ever try submit_plan?
    warning_emitted = False
    # Effective turn cap: we grant one bonus turn iff the final-turn forced
    # submit_plan fails validation, so the model gets a single shot to repair.
    bonus_turn_used = False
    turn = 0
    max_turns = cfg.max_turns

    try:
        while turn < max_turns:
            is_last = (turn == max_turns - 1)
            force = "submit_plan" if is_last else None

            # Soft warning when we're nearing the limit without any submit attempt.
            if (not warning_emitted and not submission_attempted
                    and turn >= max(0, cfg.max_turns - 3) and not is_last):
                print(
                    f"WARNING: turn {turn+1}/{cfg.max_turns}, the model hasn't tried "
                    "submit_plan yet. If the final forced submission is low-confidence, "
                    f"rerun with --max-turns {cfg.max_turns + 10}.",
                    file=sys.stderr,
                )
                warning_emitted = True

            if is_last:
                messages.append({"role": "user", "content": NUDGE_FORCE_SUBMIT})
                transcript.append(TurnLog(role="user", content=NUDGE_FORCE_SUBMIT))
                if cfg.debug_transcript:
                    _write_debug(cfg.debug_transcript, "user (nudge)", NUDGE_FORCE_SUBMIT)

            if cfg.verbose:
                print(f"[harness] turn {turn+1}/{cfg.max_turns} ({backend.name})", file=sys.stderr)

            resp: BackendResponse = backend.call(SYSTEM_PROMPT, messages, tool_schemas, force_tool=force)
            messages.append(backend.assistant_turn(resp))
            transcript.append(TurnLog(role="assistant", content=resp.text or _summarize_tool_calls(resp)))
            if cfg.debug_transcript:
                _write_debug(cfg.debug_transcript, "assistant", resp.text or _summarize_tool_calls(resp))

            if not resp.tool_calls:
                consecutive_no_tool += 1
                if consecutive_no_tool >= 2 and not is_last:
                    raise SystemExit(
                        "harness: model emitted two consecutive responses with no tool call. "
                        "Try a more capable model, or use --backend openai-json to force "
                        "the JSON-emission protocol."
                    )
                nudge = NUDGE_NO_TOOL
                messages.append({"role": "user", "content": nudge})
                transcript.append(TurnLog(role="user", content=nudge))
                if cfg.debug_transcript:
                    _write_debug(cfg.debug_transcript, "user (nudge)", nudge)
                continue
            consecutive_no_tool = 0

            terminated: StitchPlan | None = None
            for tc in resp.tool_calls:
                # Stuck detection.
                fp = _tool_call_fingerprint(tc)
                recent_calls.append(fp)
                if len(recent_calls) > 3:
                    recent_calls.pop(0)
                if len(recent_calls) == 3 and len(set(recent_calls)) == 1:
                    nudge = (
                        f"You have called {tc.name!r} with identical arguments three times. "
                        "Use a DIFFERENT tool or call submit_plan now with your best plan."
                    )
                    messages.append({"role": "user", "content": nudge})
                    transcript.append(TurnLog(role="user", content=nudge))
                    if cfg.debug_transcript:
                        _write_debug(cfg.debug_transcript, "user (stuck)", nudge)
                    recent_calls.clear()
                    continue

                if tc.name == "submit_plan":
                    submission_attempted = True
                    try:
                        terminated = StitchPlan.model_validate(tc.args)
                        msgs = backend.tool_result_turns(tc, json.dumps({"ok": True}))
                        messages.extend(msgs)
                        for m in msgs:
                            if cfg.debug_transcript:
                                _write_debug(cfg.debug_transcript, "tool_result", json.dumps(m))
                    except ValidationError as e:
                        err = json.dumps({
                            "error": "plan failed validation",
                            "details": [
                                {"loc": list(d["loc"]), "msg": d["msg"]} for d in e.errors()[:5]
                            ],
                            "hint": "Send submit_plan again with the corrections.",
                        })
                        msgs = backend.tool_result_turns(tc, err)
                        messages.extend(msgs)
                        for m in msgs:
                            transcript.append(TurnLog(role="tool", content=err, tool_name=tc.name))
                            if cfg.debug_transcript:
                                _write_debug(cfg.debug_transcript, "tool_result (validation)", err)
                        # Bonus turn: if the model's plan failed validation on
                        # the FORCED final turn, give it one extra shot rather
                        # than discarding everything.
                        if is_last and not bonus_turn_used:
                            bonus_turn_used = True
                            max_turns += 1
                            if cfg.verbose:
                                print(f"[harness] granting 1 bonus turn to repair validation error",
                                      file=sys.stderr)
                    continue

                tool = TOOLS_BY_NAME.get(tc.name)
                if tool is None:
                    err = json.dumps({
                        "error": f"unknown tool: {tc.name}",
                        "available_tools": sorted([t.name for t in TOOLS] + ["submit_plan"]),
                    })
                    msgs = backend.tool_result_turns(tc, err)
                    messages.extend(msgs)
                    transcript.append(TurnLog(role="tool", content=err, tool_name=tc.name))
                    if cfg.debug_transcript:
                        _write_debug(cfg.debug_transcript, "tool_result (unknown)", err)
                    continue

                try:
                    args_obj = tool.args_model.model_validate(tc.args)
                except ValidationError as e:
                    same_tool_fail[tc.name] = same_tool_fail.get(tc.name, 0) + 1
                    if same_tool_fail[tc.name] > 2:
                        err = json.dumps({
                            "error": f"too many validation failures for {tc.name!r}, stop using it",
                        })
                    else:
                        err = NUDGE_VALIDATION.format(
                            error=str(e.errors()[:3]),
                            schema=_minimal_repair_example(tool.args_model),
                        )
                    msgs = backend.tool_result_turns(tc, err)
                    messages.extend(msgs)
                    transcript.append(TurnLog(role="tool", content=err, tool_name=tc.name))
                    if cfg.debug_transcript:
                        _write_debug(cfg.debug_transcript, "tool_result (validation)", err)
                    continue

                try:
                    result = tool.fn(cache, args_obj)
                except Exception as e:
                    result = {"error": f"tool raised: {e}"}
                result_json = json.dumps(result)
                # Cap content fed back to the model.
                result_json_for_model = result_json[:8000]
                msgs = backend.tool_result_turns(tc, result_json_for_model)
                messages.extend(msgs)
                transcript.append(TurnLog(role="tool", content=result_json[:1000], tool_name=tc.name))
                if cfg.debug_transcript:
                    _write_debug(cfg.debug_transcript, "tool_result", result_json_for_model)

            if terminated is not None:
                return RunResult(
                    plan=terminated,
                    backend_name=backend.name,
                    turns=turn + 1,
                    transcript=transcript,
                )

            turn += 1

        raise SystemExit(
            f"loop terminated without a valid plan after {turn} turn(s). "
            f"Rerun with --max-turns {cfg.max_turns + 10} for more budget, "
            "or inspect --debug-transcript output to see what the model was doing."
        )
    finally:
        cache.close()


def _summarize_tool_calls(resp: BackendResponse) -> str:
    if not resp.tool_calls:
        return ""
    return json.dumps([{"name": tc.name, "args": tc.args} for tc in resp.tool_calls])[:1500]
