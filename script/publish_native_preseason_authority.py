#!/usr/bin/env python3
"""Publish validated FSFFL Native V2 output to the Simulator's canonical preseason contract.

The Native builder historically inherited the external preseason file as a
Sleeper-ID / coverage universe.  That means non-authoritative Razzball reference
fields can remain on veteran rows even after Native points replace the external
values.  This publisher removes that ambiguity before the Simulator consumes the
file:

* input:  native_preseason_fsffl_points.json
* output: preseason_fsffl_points.json (canonical Simulator authority)
* projected_stats is rebuilt from projected_stats_native
* external comparison/reference fields are stripped from authoritative rows
* the pre-switch canonical file is preserved as a Razzball reference snapshot
  when it is still externally sourced

No projection is recalculated here.  This is a schema/provenance publication
step only.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

RAW_TO_CANONICAL = {
    "attempts": "pass_att",
    "completions": "pass_cmp",
    "passing_yards": "pass_yd",
    "passing_tds": "pass_td",
    "interceptions": "pass_int",
    "carries": "rush_att",
    "rushing_attempts": "rush_att",
    "rushing_yards": "rush_yd",
    "rushing_tds": "rush_td",
    "receptions": "rec",
    "receiving_yards": "rec_yd",
    "receiving_tds": "rec_td",
}

EXTERNAL_REFERENCE_FIELDS = {
    "razzball_half_ppr_points_reference",
    "razzball_half_ppr_ppg_reference",
    "fallback_source_retained",
    "fallback_reason",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonical_stats(raw: dict) -> dict:
    out = {}
    for source_key, value in (raw or {}).items():
        target_key = RAW_TO_CANONICAL.get(source_key)
        if target_key:
            out[target_key] = round(float(value or 0.0), 3)
    # Native currently does not forecast fumbles; explicit zero preserves the
    # existing scoring/report schema without inventing a modeled value.
    out.setdefault("fum_lost", 0.0)
    return out


def publish(season: int, preserve_reference: bool = True) -> dict:
    sources = DATA / "simulator" / str(season) / "sources"
    native_path = sources / "native_preseason_fsffl_points.json"
    canonical_path = sources / "preseason_fsffl_points.json"
    reference_path = sources / "razzball_preseason_fsffl_points_reference.json"

    if not native_path.exists():
        raise RuntimeError(f"Missing Native production output: {native_path}")

    native = load(native_path)
    source = str(native.get("source") or "")
    players = native.get("players") or {}
    audit = native.get("audit") or {}
    if not source.startswith("FSFFL Native V2"):
        raise RuntimeError(f"Refusing to publish unexpected Native source: {source!r}")
    if not players:
        raise RuntimeError("Native production output has no players")
    if float(audit.get("native_coverage_pct") or 0.0) < 99.0:
        raise RuntimeError(f"Native coverage below publication threshold: {audit}")
    if int(audit.get("fallback_players") or 0) != 0:
        raise RuntimeError(f"Native output retains fallback projection values: {audit}")

    if preserve_reference and canonical_path.exists() and not reference_path.exists():
        prior = load(canonical_path)
        prior_source = str(prior.get("source") or "")
        if not prior_source.startswith("FSFFL Native V2"):
            shutil.copyfile(canonical_path, reference_path)

    published_players = {}
    no_native_stats = []
    for sid, original in players.items():
        row = dict(original)
        raw = row.get("projected_stats_native")
        if not isinstance(raw, dict) or not raw:
            no_native_stats.append(str(sid))
            continue
        row["projected_stats"] = canonical_stats(raw)
        for key in EXTERNAL_REFERENCE_FIELDS:
            row.pop(key, None)
        # The ECR fields are retained as descriptive QC metadata only. They do
        # not participate in the projected points and are labeled accordingly.
        if "preseason_ecr" in row or "expert_rank_sd" in row:
            row["ecr_metadata_role"] = "descriptive_qc_only_not_projection_input"
        row["source"] = str(row.get("source") or "FSFFL Native V2")
        published_players[str(sid)] = row

    if no_native_stats:
        raise RuntimeError(
            f"Cannot publish: {len(no_native_stats)} players lack Native raw stats; "
            f"examples={no_native_stats[:10]}"
        )

    payload = {
        "season": str(season),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "FSFFL Native V2 authoritative preseason projection",
        "authority": {
            "role": "SIMULATOR_PRESEASON_PLAYER_PROJECTION_AUTHORITY",
            "projection_model": native.get("source"),
            "external_projection_values_used": False,
            "published_from": str(native_path.relative_to(ROOT)),
            "external_reference_snapshot": (
                str(reference_path.relative_to(ROOT)) if reference_path.exists() else None
            ),
        },
        "players": published_players,
        "audit": {
            **audit,
            "published_players": len(published_players),
            "players_missing_native_raw_stats": 0,
            "canonical_projected_stats_from_native": True,
            "external_reference_fields_stripped": True,
        },
        "governance": {
            **(native.get("governance") or {}),
            "authoritative_simulator_input": True,
            "external_projection_values_used": False,
            "publication_recalculates_projection": False,
        },
    }
    write(canonical_path, payload)
    print(json.dumps({
        "status": "PASS",
        "season": season,
        "source": payload["source"],
        "players": len(published_players),
        "coverage_pct": audit.get("native_coverage_pct"),
        "canonical": str(canonical_path),
        "reference": str(reference_path) if reference_path.exists() else None,
    }, indent=2))
    return payload


def self_test() -> None:
    raw = {
        "attempts": 500,
        "completions": 330,
        "passing_yards": 4100,
        "passing_tds": 30,
        "interceptions": 10,
        "rushing_yards": 250,
        "rushing_tds": 3,
    }
    out = canonical_stats(raw)
    assert out["pass_att"] == 500
    assert out["pass_yd"] == 4100
    assert out["rush_yd"] == 250
    assert out["fum_lost"] == 0.0
    assert "passing_yards" not in out
    print("native preseason authority publisher self-test: PASS")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--no-preserve-reference", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
        return
    publish(args.season, preserve_reference=not args.no_preserve_reference)


if __name__ == "__main__":
    main()
