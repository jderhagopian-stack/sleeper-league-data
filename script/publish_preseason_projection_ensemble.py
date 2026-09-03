#!/usr/bin/env python3
"""Publish the provisional personal-use FSFFL 2026 preseason projection ensemble.

Evidence-respecting V1 production policy:
- QB/RB: retain the incumbent Razzball raw-stat projection. Native V2 has not
  earned replacement authority at these positions in independent historical
  testing.
- WR/TE: equal-weight Razzball and the separately validated FSFFL Native V2
  raw-stat challenger on overlapping football-stat fields. The Native WR/TE
  opportunity feature family improved all 2021-2024 rolling holdouts and then
  improved the exact independent FFToday common cohort without being selected
  on FFToday.

The equal-weight blend is deliberately conservative and provisional. Historical
Razzball frozen-series calibration is still incomplete, so learned source
weights are NOT used. This release is explicitly PERSONAL_RESEARCH only and is
swappable before any commercial deployment.

No external projection is used as a Native training target.
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

BLEND_POSITIONS = {"WR", "TE"}
INCUMBENT_POSITIONS = {"QB", "RB"}
WR_TE_EXTERNAL_WEIGHT = 0.50
WR_TE_NATIVE_WEIGHT = 0.50


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def native_canonical(raw: dict) -> dict:
    out = {}
    for source_key, value in (raw or {}).items():
        target = RAW_TO_CANONICAL.get(source_key)
        if target:
            out[target] = float(value or 0.0)
    return out


def score(stats: dict, scoring: dict) -> float:
    total = 0.0
    for key, value in (stats or {}).items():
        total += float(value or 0.0) * float(scoring.get(key, 0.0) or 0.0)
    return round(total, 3)


def blend_stats(external: dict, native_raw: dict) -> tuple[dict, dict]:
    native = native_canonical(native_raw)
    external = {str(k): float(v or 0.0) for k, v in (external or {}).items()}
    keys = sorted(set(external) | set(native))
    blended = {}
    field_policy = {}
    for key in keys:
        has_ext = key in external
        has_native = key in native
        if has_ext and has_native:
            blended[key] = (
                WR_TE_EXTERNAL_WEIGHT * external[key]
                + WR_TE_NATIVE_WEIGHT * native[key]
            )
            field_policy[key] = "equal_weight_razzball_native"
        elif has_ext:
            # Keep non-overlapping incumbent fields (notably fum_lost) rather
            # than interpreting Native's missing model as a zero forecast.
            blended[key] = external[key]
            field_policy[key] = "razzball_only_field"
        else:
            blended[key] = native[key]
            field_policy[key] = "native_only_field"
    return ({k: round(v, 3) for k, v in blended.items()}, field_policy)


def publish(season: int = 2026) -> dict:
    sources = DATA / "simulator" / str(season) / "sources"
    canonical_path = sources / "preseason_fsffl_points.json"
    native_path = sources / "native_preseason_fsffl_points.json"
    reference_path = sources / "razzball_preseason_fsffl_points_reference.json"
    league_path = DATA / "league.json"

    if not canonical_path.exists():
        raise RuntimeError(f"Missing incumbent projection file: {canonical_path}")
    if not native_path.exists():
        raise RuntimeError(f"Missing Native challenger file: {native_path}")

    incumbent = load(canonical_path)
    native = load(native_path)
    scoring = (load(league_path).get("scoring_settings") or {})

    incumbent_source = str(incumbent.get("source") or "")
    if incumbent_source.startswith("FSFFL Native") or "ENSEMBLE" in incumbent_source.upper():
        if reference_path.exists():
            incumbent = load(reference_path)
            incumbent_source = str(incumbent.get("source") or "")
        else:
            raise RuntimeError("Canonical projection is no longer a clean incumbent and no Razzball reference exists")

    native_source = str(native.get("source") or "")
    native_audit = native.get("audit") or {}
    if not native_source.startswith("FSFFL Native V2"):
        raise RuntimeError(f"Unexpected Native source: {native_source!r}")
    if float(native_audit.get("native_coverage_pct") or 0.0) < 99.0:
        raise RuntimeError(f"Native challenger coverage below threshold: {native_audit}")
    if int(native_audit.get("fallback_players") or 0) != 0:
        raise RuntimeError(f"Native challenger retains fallback projection values: {native_audit}")

    if not reference_path.exists():
        shutil.copyfile(canonical_path, reference_path)

    ext_players = {str(k): v for k, v in (incumbent.get("players") or {}).items()}
    nat_players = {str(k): v for k, v in (native.get("players") or {}).items()}
    if not ext_players:
        raise RuntimeError("Incumbent projection has no players")

    players = {}
    source_counts = {"Razzball_incumbent_QB_RB": 0, "Razzball_Native_equal_weight_WR_TE": 0}
    missing_native = []
    field_policy_counts = {}

    for sid, ext in ext_players.items():
        row = dict(ext)
        pos = str(row.get("position") or "").upper()
        nat = nat_players.get(sid)

        if pos in INCUMBENT_POSITIONS:
            row["projection_source_policy"] = "RAZZBALL_INCUMBENT"
            row["source"] = "Razzball incumbent — provisional personal-use ensemble component"
            row["ensemble_components"] = {
                "Razzball": 1.0,
                "FSFFL Native V2": 0.0,
                "reason": "Native has not earned replacement/blend weight at this position under current historical evidence."
            }
            source_counts["Razzball_incumbent_QB_RB"] += 1

        elif pos in BLEND_POSITIONS:
            if not nat or not isinstance(nat.get("projected_stats_native"), dict):
                missing_native.append(sid)
                continue
            blended, field_policy = blend_stats(
                row.get("projected_stats") or {},
                nat.get("projected_stats_native") or {},
            )
            row["projected_stats"] = blended
            row["fsffl_projected_points"] = score(blended, scoring)
            games = float(row.get("games_projected") or nat.get("games_projected") or 17.0)
            row["games_projected"] = games
            row["fsffl_projected_ppg"] = round(row["fsffl_projected_points"] / max(games, 1.0), 3)
            row["projected_stats_native"] = nat.get("projected_stats_native")
            row["native_model_version"] = nat.get("native_model_version")
            row["native_role_team"] = nat.get("native_role_team")
            row["projection_source_policy"] = "EQUAL_WEIGHT_RAZZBALL_NATIVE"
            row["source"] = "FSFFL 2026 provisional personal-use ensemble — Razzball + Native V2"
            row["ensemble_components"] = {
                "Razzball": WR_TE_EXTERNAL_WEIGHT,
                "FSFFL Native V2": WR_TE_NATIVE_WEIGHT,
                "field_policy": field_policy,
                "reason": "Native WR/TE challenger passed rolling holdouts and independent external common-cohort confirmation; equal weight is used because Razzball historical weight calibration is incomplete."
            }
            source_counts["Razzball_Native_equal_weight_WR_TE"] += 1
            for policy in field_policy.values():
                field_policy_counts[policy] = field_policy_counts.get(policy, 0) + 1
        else:
            # The Simulator preseason offensive authority is QB/RB/WR/TE. Keep
            # any other incumbent row untouched rather than inventing a policy.
            row["projection_source_policy"] = "INCUMBENT_UNCHANGED_OTHER_POSITION"

        # These old fields describe the incumbent raw source, but the canonical
        # row now has explicit component metadata. Remove ambiguous reference
        # names from the authoritative row; the full Razzball reference remains
        # available in the dedicated reference artifact.
        row.pop("razzball_half_ppr_points_reference", None)
        row.pop("razzball_half_ppr_ppg_reference", None)
        players[sid] = row

    if missing_native:
        raise RuntimeError(
            f"WR/TE blend cannot publish: {len(missing_native)} incumbent players lack Native raw stats; examples={missing_native[:10]}"
        )

    payload = {
        "season": str(season),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "FSFFL 2026 PERSONAL_RESEARCH_PROVISIONAL_ENSEMBLE",
        "authority": {
            "role": "SIMULATOR_PRESEASON_PLAYER_PROJECTION_AUTHORITY",
            "deployment_context": "PERSONAL_RESEARCH",
            "commercial_default_eligible": False,
            "policy": {
                "QB": "Razzball incumbent 100%",
                "RB": "Razzball incumbent 100%",
                "WR": "50% Razzball / 50% FSFFL Native V2 on overlapping raw stats",
                "TE": "50% Razzball / 50% FSFFL Native V2 on overlapping raw stats",
            },
            "learned_weights_used": False,
            "why_not_learned_weights": "Historical frozen Razzball series is not yet sufficient for leakage-safe category weight estimation.",
            "external_projection_used_as_native_training_target": False,
            "razzball_reference": str(reference_path.relative_to(ROOT)),
            "native_reference": str(native_path.relative_to(ROOT)),
        },
        "players": players,
        "audit": {
            "published_players": len(players),
            "source_counts": source_counts,
            "field_policy_counts": field_policy_counts,
            "native_challenger_coverage_pct": native_audit.get("native_coverage_pct"),
            "native_fallback_players": native_audit.get("fallback_players"),
            "missing_required_native_wr_te": 0,
        },
        "governance": {
            "production_scope": "PRIVATE_PERSONAL_FSFFL_ONLY",
            "personal_research_provisional": True,
            "commercial_default_eligible": False,
            "source_swappable": True,
            "external_projection_used_as_native_training_target": False,
            "QB_RB_native_replacement_rejected": True,
            "WR_TE_native_signal_validated": True,
            "equal_weight_blend_is_provisional_prior": True,
            "promotion_reassessment_required_when_historical_razzball_benchmark_available": True,
        },
    }
    write(canonical_path, payload)
    print(json.dumps({
        "status": "PASS",
        "season": season,
        "source": payload["source"],
        "players": len(players),
        "source_counts": source_counts,
        "canonical": str(canonical_path),
    }, indent=2))
    return payload


def self_test() -> None:
    ext={"rec":80,"rec_yd":1000,"rec_td":8,"fum_lost":2}
    nat={"receptions":60,"receiving_yards":800,"receiving_tds":6,"targets":90}
    out,pol=blend_stats(ext,nat)
    assert out["rec"]==70.0
    assert out["rec_yd"]==900.0
    assert out["rec_td"]==7.0
    assert out["fum_lost"]==2.0
    assert pol["rec"]=="equal_weight_razzball_native"
    assert pol["fum_lost"]=="razzball_only_field"
    print("preseason projection ensemble publisher self-test: PASS")


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--season",type=int,default=2026)
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test:
        self_test(); return
    publish(a.season)


if __name__=="__main__":
    main()
