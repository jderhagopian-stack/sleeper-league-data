#!/usr/bin/env python3
"""Build the FSFFL preseason projection baseline from normalized external sources.

Eligible independent sources receive equal weight unless future held-out evidence
shows a more complex weighting scheme improves accuracy. The builder preserves
per-source values, source disagreement, and now separately reports the strength
of historical accuracy evidence behind the source mix. A source can be useful
without being falsely described as historically validated.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List, Tuple

DATA = Path("data")
SIM_ROOT = DATA / "simulator"
REGISTRY_PATH = DATA / "projection_source_registry.json"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def resolve_sources(season: str, registry: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    available: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    for source_id, cfg in (registry.get("sources") or {}).items():
        if not cfg.get("eligible_for_ensemble", False):
            continue
        pattern = cfg.get("normalized_file_pattern")
        if not pattern:
            missing.append({"source_id": source_id, "reason": "missing_file_pattern"})
            continue
        path = Path(str(pattern).format(season=season))
        payload = load_json(path)
        if not payload:
            missing.append({"source_id": source_id, "path": str(path), "reason": "file_missing"})
            continue
        if str(payload.get("season")) != str(season):
            missing.append({"source_id": source_id, "path": str(path), "reason": "season_mismatch"})
            continue
        payload_source_id = str(payload.get("source_id") or source_id)
        if payload_source_id != source_id:
            missing.append({"source_id": source_id, "path": str(path), "reason": "source_id_mismatch"})
            continue
        if not (payload.get("players") or {}):
            missing.append({"source_id": source_id, "path": str(path), "reason": "empty_players"})
            continue
        available.append({"source_id": source_id, "config": cfg, "path": path, "payload": payload})
    return available, missing


def dedupe_independence_families(sources: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept, rejected = [], []
    families: Dict[str, str] = {}
    for source in sources:
        cfg = source["config"]
        family = str(cfg.get("independence_family") or source["source_id"])
        if family in families:
            rejected.append({"source_id": source["source_id"], "reason": "duplicate_independence_family", "independence_family": family, "already_counted_source": families[family]})
            continue
        families[family] = source["source_id"]
        kept.append(source)
    return kept, rejected


def historical_evidence_summary(sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    benchmarked = []
    unbenchmarked = []
    for source in sources:
        status = str(((source.get("config") or {}).get("historical_accuracy_evidence") or {}).get("status") or "unknown")
        if status.startswith("externally_benchmarked"):
            benchmarked.append(source["source_id"])
        else:
            unbenchmarked.append(source["source_id"])
    return {
        "externally_benchmarked_sources": benchmarked,
        "sources_without_comparable_long_run_benchmark": unbenchmarked,
        "externally_benchmarked_source_count": len(benchmarked),
    }


def build_player_ensemble(sources: List[Dict[str, Any]], minimum_sources: int = 2) -> Dict[str, Dict[str, Any]]:
    all_ids = set()
    for source in sources:
        all_ids.update(str(x) for x in (source["payload"].get("players") or {}).keys())

    output: Dict[str, Dict[str, Any]] = {}
    for sid in sorted(all_ids):
        observations, exemplar = [], None
        for source in sources:
            player = (source["payload"].get("players") or {}).get(sid)
            if not player:
                continue
            pts, ppg = player.get("fsffl_projected_points"), player.get("fsffl_projected_ppg")
            if not finite_number(pts):
                continue
            exemplar = exemplar or player
            observations.append({"source_id": source["source_id"], "points": float(pts), "ppg": float(ppg) if finite_number(ppg) else None})
        if not observations or exemplar is None:
            continue

        points = [x["points"] for x in observations]
        ppgs = [x["ppg"] for x in observations if x["ppg"] is not None]
        mean_points = statistics.fmean(points)
        mean_ppg = statistics.fmean(ppgs) if ppgs else None
        point_sd = statistics.stdev(points) if len(points) >= 2 else 0.0
        disagreement_cv = point_sd / abs(mean_points) if len(points) >= 2 and abs(mean_points) > 1e-9 else 0.0
        player_authoritative = len(observations) >= minimum_sources

        output[sid] = {
            "sleeper_id": sid,
            "player_name": exemplar.get("player_name"),
            "team": exemplar.get("team"),
            "position": exemplar.get("position"),
            "season": exemplar.get("season"),
            "fsffl_projected_points": round(mean_points, 3),
            "fsffl_projected_ppg": round(mean_ppg, 3) if mean_ppg is not None else None,
            "ensemble_method": "equal_weight_mean",
            "source_count": len(observations),
            "source_ids": [x["source_id"] for x in observations],
            "source_points": {x["source_id"]: round(x["points"], 3) for x in observations},
            "source_ppg": {x["source_id"]: (round(x["ppg"], 3) if x["ppg"] is not None else None) for x in observations},
            "source_disagreement_sd_points": round(point_sd, 3),
            "source_disagreement_cv": round(disagreement_cv, 5),
            "authoritative_projection_allowed": player_authoritative,
            "authority_reason": "minimum_independent_sources_met" if player_authoritative else "insufficient_player_level_independent_sources",
        }
    return output


def main():
    league, registry = load_json(DATA / "league.json"), load_json(REGISTRY_PATH)
    if not league:
        raise RuntimeError("Missing data/league.json")
    if not registry:
        raise RuntimeError("Missing data/projection_source_registry.json")
    season = str(league.get("season") or "").strip()
    if not season:
        raise RuntimeError("Active season missing from data/league.json")

    available, missing = resolve_sources(season, registry)
    independent, duplicate_family_rejections = dedupe_independence_families(available)
    policy = registry.get("policy") or {}
    minimum = int(policy.get("minimum_independent_sources_for_authoritative_ensemble", 2))
    preferred = int(policy.get("preferred_independent_sources_for_high_confidence_ensemble", minimum))
    source_gate = len(independent) >= minimum
    high_confidence_source_depth = len(independent) >= preferred
    evidence = historical_evidence_summary(independent)
    players = build_player_ensemble(independent, minimum_sources=minimum)
    authoritative_players = sum(1 for x in players.values() if x["authoritative_projection_allowed"])
    player_authority_rate = authoritative_players / len(players) if players else 0.0
    minimum_player_coverage = float(policy.get("minimum_player_authority_coverage_for_production", 0.85))
    authoritative = source_gate and player_authority_rate >= minimum_player_coverage

    sim_dir = SIM_ROOT / season
    sources_dir, outputs_dir = sim_dir / "sources", sim_dir / "outputs"
    payload = {
        "season": season,
        "source": "FSFFL governed multi-source ensemble",
        "model_version": "FSFFL-Projection-Ensemble-1.2",
        "ensemble_method": "equal_weight_mean",
        "authoritative_projection_allowed": authoritative,
        "minimum_independent_sources": minimum,
        "preferred_independent_sources_for_high_confidence_ensemble": preferred,
        "high_confidence_source_depth_reached": high_confidence_source_depth,
        "historical_accuracy_evidence": evidence,
        "minimum_player_authority_coverage_for_production": minimum_player_coverage,
        "player_authority_coverage": round(player_authority_rate, 5),
        "independent_sources_used": [x["source_id"] for x in independent],
        "scoring_source": "data/league.json (normalized by each source adapter before ensemble)",
        "players": players,
    }
    audit = {
        "season": season,
        "model_version": "FSFFL-Projection-Ensemble-1.2",
        "policy": policy,
        "available_registered_sources": [x["source_id"] for x in available],
        "independent_sources_used": [x["source_id"] for x in independent],
        "historical_accuracy_evidence": evidence,
        "high_confidence_source_depth_reached": high_confidence_source_depth,
        "missing_or_invalid_sources": missing,
        "duplicate_information_rejections": duplicate_family_rejections,
        "player_count": len(players),
        "players_with_authoritative_multi_source_projection": authoritative_players,
        "players_without_authoritative_multi_source_projection": len(players) - authoritative_players,
        "player_authority_coverage": round(player_authority_rate, 5),
        "authoritative_projection_allowed": authoritative,
        "quality_gate": {
            "minimum_independent_sources": minimum,
            "independent_sources_available": len(independent),
            "source_gate_passed": source_gate,
            "preferred_independent_sources_for_high_confidence_ensemble": preferred,
            "high_confidence_source_depth_reached": high_confidence_source_depth,
            "minimum_player_authority_coverage_for_production": minimum_player_coverage,
            "player_authority_coverage": round(player_authority_rate, 5),
            "passed": authoritative,
        },
        "governance_note": "Production authority and historical evidence strength are intentionally separate. Two independent sources can clear the operational floor, but the audit also reports whether the preferred three-source depth and comparable long-run benchmark support are present. Equal weighting remains the default because the identified 2014-2025 external benchmark found simple averaging more robust than historical-accuracy weighting."
    }

    write_json(sources_dir / "preseason_fsffl_points_candidate_ensemble.json", payload)
    write_json(outputs_dir / "projection_ensemble_audit.json", audit)
    if authoritative:
        write_json(sources_dir / "preseason_fsffl_points.json", payload)
        print(f"Authoritative FSFFL ensemble built from {len(independent)} independent sources for {len(players)} players; player authority coverage={player_authority_rate:.1%}; high-confidence source depth={high_confidence_source_depth}.")
    else:
        print(f"Candidate ensemble built; production authority gate failed. independent_sources={len(independent)}/{minimum}, player_authority_coverage={player_authority_rate:.1%}/{minimum_player_coverage:.1%}. Production baseline was not overwritten.")


if __name__ == "__main__":
    main()
