#!/usr/bin/env python3
"""FSFFL Alternate History 0.5e: timestamp-safe modified-trade branch expansion.

Consumes the queue-contract validated 0.5d v2 structural trade policy and expands
only the `modified_trade_branch` outcome into a small set of concrete player-swap
packages that can be consumed by the multi-season branch engine.

Historical-safety invariants:
- no current GM 3.0 values or current market ranks;
- no future NFL outcomes;
- player replacement quality uses fantasy scoring from completed weeks strictly
  before the transaction week;
- candidates must actually be owned by the historical sender in the alternate
  pre-transaction state;
- historical pick / FAAB legs are preserved in this first expander rather than
  inventing replacement draft capital without point-in-time evidence.

This is intentionally conservative. A generated package is a mechanically and
historically plausible candidate, not a claim that the assets had equal market
value. The branch propagator may retain a no-trade outcome when no defensible
replacement package exists.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_trade_policy import owner_of, player_positions
from run_fsffl_historical_trade_policy_v2 import run as run_trade_policy
from run_fsffl_historical_usage_policy import HistoricalPoints

MAX_CANDIDATES_PER_ASSET = 3
MAX_PACKAGES_PER_TRADE = 8


def event_season_week(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    season = event.get("source_season") or event.get("season")
    week = event.get("leg") or event.get("week")
    try:
        parsed_week = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed_week = None
    return (str(season) if season is not None else None, parsed_week)


def signal(points: HistoricalPoints, season: Optional[str], week: Optional[int], pid: str) -> Dict[str, Any]:
    if season is None:
        return {"score": None, "observations": 0, "reason": "missing_source_season"}
    return points.trailing(season, week, str(pid))


def replacement_weights(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    logits = []
    for row in rows:
        delta = row.get("trailing_score_delta")
        obs = int(row.get("combined_observations") or 0)
        if delta is None:
            logit = -1.8
        else:
            # Historical production is only a coarse timestamp-safe equivalence
            # proxy. Prefer closer players without pretending it is market value.
            logit = -min(4.0, abs(float(delta)) / 4.0)
        if obs >= 4:
            logit += 0.35
        elif obs == 0:
            logit -= 0.4
        logits.append(logit)
    mx = max(logits)
    exps = [math.exp(max(-20.0, min(20.0, x - mx))) for x in logits]
    denom = sum(exps) or 1.0
    for row, weight in zip(rows, exps):
        row["conditional_probability"] = round(weight / denom, 4)
    rows.sort(key=lambda x: (x["conditional_probability"], x["player_id"]), reverse=True)
    return rows


def candidate_replacements(
    alt_pre: ah.LeagueState,
    sender: str,
    target_pid: str,
    event_players: set[str],
    positions: Dict[str, str],
    points: HistoricalPoints,
    season: Optional[str],
    week: Optional[int],
) -> Dict[str, Any]:
    target_pos = positions.get(str(target_pid), "")
    target_signal = signal(points, season, week, target_pid)
    target_score = target_signal.get("score")
    candidates: List[Dict[str, Any]] = []
    for pid in sorted(alt_pre.roster_players.get(str(sender), set())):
        pid = str(pid)
        if pid == str(target_pid) or pid in event_players:
            continue
        if target_pos and positions.get(pid, "") != target_pos:
            continue
        cand_signal = signal(points, season, week, pid)
        cand_score = cand_signal.get("score")
        delta = None
        if target_score is not None and cand_score is not None:
            delta = round(float(cand_score) - float(target_score), 4)
        candidates.append({
            "player_id": pid,
            "position": positions.get(pid, ""),
            "trailing_signal": cand_signal,
            "target_trailing_signal": target_signal,
            "trailing_score_delta": delta,
            "combined_observations": int(cand_signal.get("observations") or 0) + int(target_signal.get("observations") or 0),
        })
    replacement_weights(candidates)
    return {
        "target_player_id": str(target_pid),
        "sender_roster_id": str(sender),
        "position": target_pos,
        "target_trailing_signal": target_signal,
        "candidates": candidates[:MAX_CANDIDATES_PER_ASSET],
    }


def choose_targets(
    event: Dict[str, Any],
    decision: Dict[str, Any],
    alt_pre: ah.LeagueState,
    positions: Dict[str, str],
) -> List[Tuple[str, str]]:
    """Return (target_player, historical_sender) pairs worth modifying."""
    drops = {str(pid): str(rid) for pid, rid in (event.get("drops") or {}).items()}
    direct = {str(x) for x in decision.get("direct_divergent_assets_in_trade") or []}
    same_positions = {str(x) for x in decision.get("same_divergent_positions") or []}
    divergent_participants = {str(x) for x in decision.get("divergent_participants") or []}

    targets: List[Tuple[str, str]] = []
    for pid, sender in drops.items():
        unavailable = owner_of(alt_pre, pid) != sender
        direct_asset = pid in direct
        positional_context = sender in divergent_participants and positions.get(pid, "") in same_positions
        if unavailable or direct_asset or positional_context:
            targets.append((pid, sender))

    # Avoid modifying every player in a large historical package merely because
    # the roster context changed. Exact-leg impossibility/direct divergence takes
    # priority; otherwise modify at most one positionally sensitive outgoing leg.
    hard = [(pid, rid) for pid, rid in targets if owner_of(alt_pre, pid) != rid or pid in direct]
    return hard if hard else targets[:1]


def expand_trade(
    adapter: FSFFLHistoricalAdapter,
    event: Dict[str, Any],
    decision: Dict[str, Any],
    positions: Dict[str, str],
    points: HistoricalPoints,
) -> Dict[str, Any]:
    created = int(event.get("created") or 0)
    alt_pre = ah.apply_fork(ah.reconstruct_state(adapter, created), _SCENARIO)
    season, week = event_season_week(event)
    event_players = {str(x) for x in (event.get("adds") or {}).keys()} | {str(x) for x in (event.get("drops") or {}).keys()}
    targets = choose_targets(event, decision, alt_pre, positions)

    target_rows = [
        candidate_replacements(alt_pre, sender, pid, event_players, positions, points, season, week)
        for pid, sender in targets
    ]

    viable = [row for row in target_rows if row.get("candidates")]
    packages: List[Dict[str, Any]] = []
    if viable and len(viable) == len(target_rows):
        candidate_lists = [row["candidates"] for row in target_rows]
        for combo in itertools.product(*candidate_lists):
            replacements = []
            joint = 1.0
            for target, cand in zip(target_rows, combo):
                joint *= float(cand.get("conditional_probability") or 0.0)
                replacements.append({
                    "outgoing_historical_player_id": target["target_player_id"],
                    "sender_roster_id": target["sender_roster_id"],
                    "replacement_player_id": cand["player_id"],
                    "position": cand.get("position"),
                    "trailing_score_delta": cand.get("trailing_score_delta"),
                })
            packages.append({
                "replacements": replacements,
                "raw_joint_weight": joint,
            })
        packages.sort(key=lambda x: x["raw_joint_weight"], reverse=True)
        packages = packages[:MAX_PACKAGES_PER_TRADE]
        denom = sum(float(x["raw_joint_weight"]) for x in packages) or 1.0
        for idx, package in enumerate(packages, 1):
            package["package_id"] = f"{decision['transaction_id']}:modified:{idx}"
            package["conditional_probability_given_modified"] = round(float(package.pop("raw_joint_weight")) / denom, 4)
            package["preserve_historical_pick_legs"] = True
            package["preserve_historical_faab_legs"] = True
            package["historical_market_value_equivalence_claimed"] = False

    observations = sum(
        int(c.get("combined_observations") or 0)
        for row in target_rows for c in (row.get("candidates") or [])[:1]
    )
    if not targets:
        status = "NO_PLAYER_LEG_TARGET_IDENTIFIED"
        confidence = "LOW"
    elif not packages:
        status = "NO_DEFENSIBLE_CONCRETE_PACKAGE"
        confidence = "LOW"
    else:
        status = "CONCRETE_PLAYER_SWAP_CANDIDATES"
        confidence = "MEDIUM" if observations >= 2 else "LOW"

    return {
        "transaction_id": decision["transaction_id"],
        "timestamp_ms": created,
        "season": season,
        "week": week,
        "modified_branch_probability": float((decision.get("probabilities") or {}).get("modified_trade_branch") or 0.0),
        "classification": decision.get("classification"),
        "expansion_status": status,
        "confidence": confidence,
        "target_assets": target_rows,
        "packages": packages,
        "design_note": "Packages repair or perturb player legs only; pick/FAAB replacements are not invented without timestamp-safe evidence.",
    }


def run(scenario_path: Path) -> Path:
    global _SCENARIO
    adapter = FSFFLHistoricalAdapter()
    payload = load(scenario_path)
    _SCENARIO = ah.scenario_from_json(adapter, payload)
    policy = load(run_trade_policy(scenario_path))
    event_by_id = {str(e.get("transaction_id")): e for e in adapter.completed_events()}
    positions = player_positions()
    points = HistoricalPoints()

    expansions = []
    for decision in policy.get("decisions") or []:
        if float((decision.get("probabilities") or {}).get("modified_trade_branch") or 0.0) <= 0.0:
            continue
        tid = str(decision.get("transaction_id") or "")
        event = event_by_id.get(tid)
        if event is None:
            raise ah.AlternateHistoryError(f"0.5e missing trade event {tid}")
        expansions.append(expand_trade(adapter, event, decision, positions, points))

    concrete = sum(1 for x in expansions if x["packages"])
    unresolved = sum(1 for x in expansions if not x["packages"])
    total_packages = sum(len(x["packages"]) for x in expansions)
    report = {
        "model_version": "Fantasy-Alternate-History-0.5e-modified-trade-expansion",
        "scenario_id": _SCENARIO.scenario_id,
        "design_invariants": {
            "current_gm3_numeric_values_used": False,
            "current_market_values_used": False,
            "future_nfl_outcomes_used": False,
            "completed_prior_week_scoring_only": True,
            "candidate_must_be_owned_at_trade_timestamp": True,
            "historical_pick_and_faab_legs_preserved": True,
            "market_value_equivalence_claimed": False,
        },
        "modified_trade_events_considered": len(expansions),
        "events_with_concrete_packages": concrete,
        "events_without_concrete_packages": unresolved,
        "concrete_package_count": total_packages,
        "historical_points_sources": points.sources,
        "expansions": expansions,
    }
    out = ah.write_isolated_json(
        f"results/{_SCENARIO.scenario_id}/historical_trade_expansion_0_5e.json", report
    )
    print(out)
    print(json.dumps({
        "modified_trade_events_considered": len(expansions),
        "events_with_concrete_packages": concrete,
        "events_without_concrete_packages": unresolved,
        "concrete_package_count": total_packages,
    }, indent=2, sort_keys=True))
    return out


_SCENARIO: ah.Scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand 0.5d modified historical trade branches")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
