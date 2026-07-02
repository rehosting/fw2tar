# fw2tar test suites

Four layers, cheapest first. Per-PR CI (`.github/workflows/build.yaml`) runs
everything except the full behavior matrix and the full firmware sweep, which
run nightly (`nightly.yaml`).

| Suite | What it proves | Fixtures | CI |
|---|---|---|---|
| `cargo test` | Rust unit logic (naming, manifest framing, candidate selection) | none | per-PR |
| `behavior/run.sh` | metadata fidelity (modes, suid/sgid/sticky, symlinks) per filesystem × extractor; see `BEHAVIOR.md` | synthetic | core types per-PR, full matrix nightly |
| `contract/run.sh` | the **output-layout contract** consumers parse: exact files left in the output dir (winner, per-candidate archives, manifest sidecars, `--primary-limit` secondaries, nothing else), manifest sidecar ≡ embedded gzip trailer, default naming | synthetic | per-PR |
| `container/run.sh` | container interface: banner, installers, entry points, plus error paths (exit 1 for fatal arg errors, exit 2 for "No extractor succeeded.", no stray outputs on failure) | synthetic | per-PR |
| `end_to_end.sh` | full pipeline on real vendor firmware, diffed against committed baselines in `results/`; every run also validates the manifest contract and baselines any advertised secondary filesystem | downloads | 2-firmware smoke per-PR, full sweep nightly |

## end_to_end.sh usage

```sh
./end_to_end.sh                       # all firmware (nightly)
./end_to_end.sh tl_wr841n rb750gr3    # named subset (per-PR smoke)
./end_to_end.sh --update google_wifi_multi   # (re)create baselines
```

Baseline diffs are tiered: differences on rootfs-viability paths (`bin/`,
`etc/`, `lib/`, `init`, …) and any mode/type/symlink-target change are
reported as `[CRITICAL]`, the rest as `[drift]` (`compare_json.py
--critical`). Both still fail the run; the tiers make failures and `--update`
reviews triageable.

## Contract helper

`contract/check_contract.py <primary.rootfs.tar.gz>` validates the manifest
sidecar and the copy embedded in the archive's gzip trailer, `input_hash`
against the firmware, and that every advertised secondary filesystem resolves
(`--secondaries N` to pin a count, `auto` to accept what's advertised). It is
reused by `end_to_end.sh` on real firmware.
