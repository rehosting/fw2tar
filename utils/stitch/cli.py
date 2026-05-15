"""CLI for the fw2tar stitcher.

Subcommands:
  shard  - run an extractor on a firmware blob and emit per-shard .tar.gz + manifest
  plan   - drive an LLM to produce a stitch_plan.yaml from a shard directory
  apply  - apply a stitch_plan.yaml (LLM-produced or human-edited) to build the unified tar
  all    - shard -> plan -> apply, end-to-end
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .harness import HarnessConfig, run
from .plan import apply_plan, dump_plan, load_plan


# Commands that perform on-disk extraction and therefore need fakeroot so that
# uid/gid metadata from the firmware survives into the shard tarballs. `plan`
# and `apply` are read-only / tar-header-only and don't need it.
_FAKEROOT_CMDS = {"shard", "all"}


def _under_fakeroot_or_root() -> bool:
    return os.environ.get("FAKEROOTKEY") is not None or os.geteuid() == 0


def _reexec_under_fakeroot(cmd_name: str) -> None:
    """If the requested command needs fakeroot and we're not already inside
    one (or root), re-exec ourselves through `fakeroot --`. Carries argv and
    env through transparently. No-op if --no-fakeroot was passed.
    """
    if cmd_name not in _FAKEROOT_CMDS:
        return
    if "--no-fakeroot" in sys.argv:
        return
    if _under_fakeroot_or_root():
        return
    if not shutil.which("fakeroot"):
        print(
            "WARNING: 'fakeroot' is not on PATH. Extraction will run with your "
            "real uid/gid, so firmware file ownership (root, setuid binaries, "
            "etc.) will be LOST in the shard tarballs. Install fakeroot, or "
            "pass --no-fakeroot to suppress this warning.",
            file=sys.stderr,
        )
        return
    # We were invoked via either `python -m stitch ...` (sys.argv[0] points
    # at __main__.py) or via the fwstitch shim (sys.argv[0] points at it).
    # Either way, re-launching the same invocation under fakeroot is what we
    # want, so just prepend `fakeroot --` to argv.
    new_argv = ["fakeroot", "--", sys.executable] + ["-m", "stitch"] + sys.argv[1:]
    os.execvp("fakeroot", new_argv)


# --------------- helpers ---------------

def _resolve_llm_env(args) -> tuple[str | None, str, str]:
    base_url = args.base_url or os.environ.get("LLM_BASE_URL")
    api_key = args.api_key or os.environ.get("LLM_API_KEY") or "dummy"
    model = args.model or os.environ.get("LLM_MODEL")
    if not model:
        raise SystemExit("--model not given and LLM_MODEL not set")
    return base_url, api_key, model


def _resolve_insecure(args) -> bool:
    if getattr(args, "insecure", False):
        return True
    return os.environ.get("LLM_INSECURE", "").lower() in ("1", "true", "yes")


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", default=None, help="LLM model name (else $LLM_MODEL)")
    p.add_argument("--base-url", default=None, help="OpenAI-compatible endpoint URL (else $LLM_BASE_URL)")
    p.add_argument("--api-key", default=None, help="API key (else $LLM_API_KEY, defaults to 'dummy')")
    p.add_argument("--max-turns", type=int, default=10)
    p.add_argument("--no-native-tools", action="store_true",
                   help="Skip native tool-calling, use JSON fallback mode")
    p.add_argument("-k", "--insecure", action="store_true",
                   help="Skip TLS cert verification (for self-signed self-hosted models). "
                        "Also honored via env: LLM_INSECURE=1.")


def _add_apply_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--on-conflict", choices=["base", "overlay", "error"], default="overlay",
                   help="Path collision policy (default: overlay wins)")
    p.add_argument("--strict", action="store_true", help="Alias for --on-conflict error")
    p.add_argument("--force", action="store_true", help="Apply even if confidence=low")


def _print_plan_summary(plan) -> None:
    print(f"[stitch] plan confidence: {plan.confidence}")
    for f in plan.fragments:
        extra = f"  ({f.fs_type})" if f.fs_type else ""
        print(f"  {f.role:7s} {f.mount_point:25s} <- {f.source}{extra}")
    if plan.open_questions:
        print("[stitch] open questions:")
        for q in plan.open_questions:
            print(f"  - {q}")


def _print_apply_summary(stats: dict) -> None:
    print(f"[stitch] applied: {stats['members_written']} members, "
          f"{stats['conflicts']} conflicts -> {stats['out_path']}")
    if stats["conflict_samples"]:
        print("[stitch] sample conflicts (path, kept_from, replaced_by):")
        for path, kept, repl in stats["conflict_samples"]:
            print(f"  {path}: {kept} <- {repl}")


def _default_out(frag_dir: Path) -> Path:
    return frag_dir / f"{frag_dir.resolve().name}.stitched.rootfs.tar.gz"


# --------------- subcommand handlers ---------------

def cmd_shard(args) -> int:
    from .shard import shard
    summary = shard(
        firmware=args.firmware,
        out_dir=args.out,
        extractor=args.extractor,
        extracted_dir=args.from_extracted,
        min_score=args.min_score,
        reextract=not args.no_reextract,
        verbose=args.verbose,
    )
    print(f"[shard] wrote {summary['count']} shards to {summary['shard_dir']}")
    if summary.get("reextracted_count"):
        print(f"[shard] re-extracted {summary['reextracted_count']} shard(s) with "
              f"native tools (perms preserved)")
    print(f"[shard] manifest: {summary['manifest']}")
    if args.verbose:
        for s in summary["shards"]:
            rx = f"  reextracted_with={s['reextracted_with']}" if s.get('reextracted_with') else ""
            print(f"  {s['name']}  score={s['score']}  fs_type={s['fs_type_guess']}  "
                  f"root_path={s['root_path']}{rx}")
    if summary["count"] == 0:
        print("[shard] no shards found. Try lowering --min-score or pre-extracting "
              "and pointing with --from-extracted.", file=sys.stderr)
        return 2
    return 0


def cmd_plan(args) -> int:
    base_url, api_key, model = _resolve_llm_env(args)
    cfg = HarnessConfig(
        base_url=base_url, api_key=api_key, model=model,
        max_turns=args.max_turns, force_fallback=args.no_native_tools,
        insecure=_resolve_insecure(args), verbose=args.verbose,
    )
    result = run(args.shard_dir, cfg)
    plan_out = args.plan_out or (args.shard_dir / "stitch_plan.yaml")
    dump_plan(result.plan, plan_out)
    print(f"[plan] wrote {plan_out} ({result.turns} turns, "
          f"{'fallback' if result.used_fallback else 'native'} mode)")
    _print_plan_summary(result.plan)
    return 0


def cmd_apply(args) -> int:
    plan = load_plan(args.plan)
    if plan.confidence == "low" and not args.force:
        print("[apply] plan confidence is 'low' — refusing. Re-run with --force.",
              file=sys.stderr)
        return 2
    on_conflict = "error" if args.strict else args.on_conflict
    out_path = args.out or _default_out(args.shard_dir)
    stats = apply_plan(plan, args.shard_dir, out_path,
                       on_conflict=on_conflict, verbose=args.verbose)
    _print_apply_summary(stats)
    return 0


def cmd_all(args) -> int:
    """shard -> plan -> apply in one go. Useful for batch jobs."""
    from .shard import shard
    summary = shard(
        firmware=args.firmware,
        out_dir=args.shard_dir,
        extractor=args.extractor,
        min_score=args.min_score,
        reextract=not args.no_reextract,
        verbose=args.verbose,
    )
    print(f"[all] {summary['count']} shards extracted")
    if summary["count"] == 0:
        return 2

    base_url, api_key, model = _resolve_llm_env(args)
    cfg = HarnessConfig(
        base_url=base_url, api_key=api_key, model=model,
        max_turns=args.max_turns, force_fallback=args.no_native_tools,
        insecure=_resolve_insecure(args), verbose=args.verbose,
    )
    result = run(args.shard_dir, cfg)
    plan_out = args.shard_dir / "stitch_plan.yaml"
    dump_plan(result.plan, plan_out)
    _print_plan_summary(result.plan)

    if not args.no_apply:
        if result.plan.confidence == "low" and not args.force:
            print("[all] confidence=low — not applying. Re-run with --force or "
                  "use --no-apply.", file=sys.stderr)
            return 2
        on_conflict = "error" if args.strict else args.on_conflict
        out_path = args.out or _default_out(args.shard_dir)
        stats = apply_plan(result.plan, args.shard_dir, out_path,
                           on_conflict=on_conflict, verbose=args.verbose)
        _print_apply_summary(stats)
    return 0


# --------------- top-level parser ---------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fw2tar.utils.stitch",
        description="LLM-driven multi-shard filesystem stitching for fw2tar firmware analysis.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # shard
    sp = sub.add_parser("shard", help="extract a firmware blob into per-shard tarballs + manifest")
    sp.add_argument("firmware", type=Path, nargs="?", help="firmware blob (omit if --from-extracted)")
    sp.add_argument("-o", "--out", type=Path, required=True, help="shard output directory")
    sp.add_argument("--extractor", choices=["unblob", "binwalk"], default="unblob")
    sp.add_argument("--from-extracted", type=Path, default=None,
                    help="skip extraction; walk this pre-extracted tree directly")
    sp.add_argument("--min-score", type=int, default=3,
                    help="extra-candidate floor for the score-based pass (default 3). "
                         "Selection primarily uses unblob's *_extract naming; this only "
                         "matters for trees that don't follow that convention.")
    sp.add_argument("--no-reextract", action="store_true",
                    help="Skip native re-extraction (cpio etc.) — keeps unblob/binwalk's "
                         "7z output even though 7z corrupts permissions on cpio.")
    sp.add_argument("--no-fakeroot", action="store_true",
                    help="Don't re-exec under fakeroot. Without fakeroot, firmware uid/gid "
                         "ownership (e.g. files owned by root) is lost in the shard tarballs.")
    sp.set_defaults(func=cmd_shard)

    # plan
    sp = sub.add_parser("plan", help="drive an LLM to produce stitch_plan.yaml")
    sp.add_argument("shard_dir", type=Path)
    sp.add_argument("--plan-out", type=Path, default=None,
                    help="output YAML (default: <shard_dir>/stitch_plan.yaml)")
    _add_llm_args(sp)
    sp.set_defaults(func=cmd_plan)

    # apply
    sp = sub.add_parser("apply", help="build the stitched .tar.gz from a stitch_plan.yaml")
    sp.add_argument("shard_dir", type=Path)
    sp.add_argument("plan", type=Path, help="stitch_plan.yaml")
    sp.add_argument("--out", type=Path, default=None,
                    help="output .tar.gz (default: <shard_dir>/<name>.stitched.rootfs.tar.gz)")
    _add_apply_args(sp)
    sp.set_defaults(func=cmd_apply)

    # all
    sp = sub.add_parser("all", help="shard -> plan -> apply end-to-end")
    sp.add_argument("firmware", type=Path)
    sp.add_argument("--shard-dir", type=Path, required=True)
    sp.add_argument("--out", type=Path, default=None)
    sp.add_argument("--extractor", choices=["unblob", "binwalk"], default="unblob")
    sp.add_argument("--min-score", type=int, default=3)
    sp.add_argument("--no-reextract", action="store_true")
    sp.add_argument("--no-fakeroot", action="store_true")
    sp.add_argument("--no-apply", action="store_true", help="stop after plan, don't build stitched tar")
    _add_llm_args(sp)
    _add_apply_args(sp)
    sp.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Peek at the subcommand before parsing so we can re-exec under fakeroot
    # without losing flags or burning argparse on a doomed parse. argv=None
    # means "use sys.argv", which is the normal case where re-exec applies.
    if argv is None:
        cmd_name = next((a for a in sys.argv[1:] if not a.startswith("-")), "")
        _reexec_under_fakeroot(cmd_name)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "shard":
        if args.firmware is None and args.from_extracted is None:
            parser.error("shard: provide either FIRMWARE or --from-extracted")

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
