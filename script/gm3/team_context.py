#!/usr/bin/env python3
"""Publish governed GM3 player context for a selected viewing team.

The output is an analytical input for League Intelligence. It evaluates an
explicit zero-price roster counterfactual and does not estimate a fair price,
trade acceptance, or recommended action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from team_improvement import MODEL_VERSION as TEAM_IMPROVEMENT_MODEL_VERSION
from team_improvement import portfolio_evaluator


MODEL_VERSION = "FSFFL-GM3-Team-Context-1.0"
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _governed_source_paths(data_dir: Path, season: str) -> list[Path]:
    paths = [
        data_dir / "fsffl_asset_values.json",
        data_dir / "league.json",
        data_dir / "rosters.json",
        data_dir / "users.json",
        data_dir / "players.json",
        data_dir / "simulator" / season / "inputs" / "player_weekly_projections.json",
        data_dir / "stats" / "fsffl" / season / "league_matchups_raw.json",
        data_dir / "gm" / "league" / "simulator_context.json",
        data_dir / "gm" / "state_weight_calibration.json",
        data_dir / "gm" / "franchise_index.json",
    ]
    franchise_path = data_dir / "gm" / "franchise_index.json"
    if franchise_path.exists():
        franchise = _load(franchise_path)
        for team in (franchise.get("teams") or []):
            profile = ((team.get("paths") or {}).get("strategic_asset_profiles"))
            if profile:
                candidate = Path(profile)
                paths.append(candidate if candidate.is_absolute() else ROOT / candidate)
    return sorted({path.resolve() for path in paths if path.exists()}, key=str)


def _compact_perspective(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    utility = value.get("shared_decision_utility") or {}
    strategic = value.get("strategic_context") or {}
    posture = strategic.get("strategic_posture_resolution") or {}
    weight_resolution = strategic.get("weight_resolution") or {}
    cuts = (value.get("roster_resolution") or {}).get("selected_cuts") or []
    return {
        "user_id": value.get("user_id"),
        "shared_decision_utility": {
            "score": utility.get("score"),
            "components": utility.get("components") or {},
            "primitive_blocks": utility.get("primitive_blocks") or {},
            "objective_weights": utility.get("objective_weights") or {},
            "objective_weights_before_channel_authorization": (
                utility.get("objective_weights_before_channel_authorization") or {}
            ),
            "incremental_channel_authorization": (
                utility.get("incremental_channel_authorization") or {}
            ),
            "model_version": utility.get("model_version"),
        },
        "simulator_delta": value.get("simulator_delta") or {},
        "competitive_state": strategic.get("competitive_state"),
        "competitive_strength_inputs": weight_resolution.get("inputs") or {},
        "strategic_posture": strategic.get("strategic_posture"),
        "strategic_posture_source": strategic.get("strategic_posture_source"),
        "active_objective_weights": strategic.get("objective_weights") or {},
        "calculated_state_objective_weights": (
            strategic.get("calculated_state_objective_weights") or {}
        ),
        "objective_weight_basis": posture.get("posture_weight_basis"),
        "endogenous_cuts": [
            {
                "player_id": row.get("player_id"),
                "name": row.get("name"),
                "position": row.get("position"),
            }
            for row in cuts
        ],
        "decision_attribution": value.get("decision_attribution") or {},
    }


def build_team_context(
    focus_user_id: str,
    *,
    data_dir: Path = DATA,
    simulations: int = 1000,
    seed: int = 20260821,
    strategic_posture: str = "AUTO",
    limit: int | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    asset_path = data_dir / "fsffl_asset_values.json"
    projection_path = data_dir / "simulator" / "2026" / "inputs" / "player_weekly_projections.json"
    roster_path = data_dir / "rosters.json"
    simulator_path = data_dir / "gm" / "league" / "simulator_context.json"
    assets = _load(asset_path)
    projections = _load(projection_path)
    season = str(projections.get("season") or "2026")
    projected_ids = {str(x) for x in (projections.get("players") or {})}
    players = list(assets.get("players") or [])
    if limit is not None:
        players = players[:max(0, int(limit))]

    evaluator = portfolio_evaluator(
        str(focus_user_id),
        simulations=int(simulations),
        seed=int(seed),
        strategic_posture=str(strategic_posture or "AUTO"),
    )
    records = []
    for player in players:
        player_id = str(player.get("player_id") or "")
        identity = {
            "player_id": player_id,
            "name": player.get("name"),
            "position": player.get("position"),
            "nfl_team": player.get("nfl_team"),
            "current_owner_user_id": player.get("current_owner_user_id"),
            "current_owner_manager": player.get("current_owner_manager"),
            "current_owner_team": player.get("current_owner_team"),
        }
        if not player_id or player_id not in projected_ids:
            records.append({
                **identity,
                "available": False,
                "reason": "player lacks a canonical Simulator projection",
            })
            continue
        context = evaluator.evaluate_player_context(player_id)
        records.append({
            **identity,
            "available": True,
            "scenario": context.get("scenario"),
            "focal_team_context": _compact_perspective(context.get("focal_team_context")),
            "current_owner_context": _compact_perspective(context.get("current_owner_context")),
            "zero_price_counterfactual": True,
            "creates_trade_price": False,
            "creates_acceptance_probability": False,
            "recommendation": False,
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "authority": "GM3 Team Improvement",
        "team_improvement_model_version": TEAM_IMPROVEMENT_MODEL_VERSION,
        "shared_decision_utility": "FSFFL-Shared-Decision-Utility-2.0",
        "focus_user_id": str(focus_user_id),
        "strategic_posture_request": str(strategic_posture or "AUTO"),
        "simulation_count": int(simulations),
        "seed": int(seed),
        "scenario_contract": {
            "purpose": "gross marginal roster context for human investigation",
            "zero_price_counterfactual": True,
            "fair_price_estimate": False,
            "willingness_to_pay_estimate": False,
            "trade_acceptance_probability": False,
            "recommendation": False,
            "roster_legalization_and_lineup_reoptimization": True,
        },
        "source_hashes": {
            str(path.relative_to(ROOT)): _sha256(path)
            for path in _governed_source_paths(data_dir, season)
        },
        "source_hash_policy": "all current model/data inputs used by this publisher must match",
        "record_count": len(records),
        "available_record_count": sum(bool(row.get("available")) for row in records),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--focus-user-id", required=True)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--simulations", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260821)
    parser.add_argument("--strategic-posture", default="AUTO")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_team_context(
        args.focus_user_id,
        data_dir=args.data_dir,
        simulations=args.simulations,
        seed=args.seed,
        strategic_posture=args.strategic_posture,
        limit=args.limit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "focus_user_id": payload["focus_user_id"],
        "records": payload["record_count"],
        "available_records": payload["available_record_count"],
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
