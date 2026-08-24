#!/usr/bin/env python3
"""FSFFL Alternate History 0.7b: stable branch replay with fork-season Max PF.

Builds on 0.7a v2 numerical stability and makes accumulated fork-season Max PF
part of branch identity before equivalent-state merging. Branches that later
converge to the same roster remain distinct when their historical season
consequences differ.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import alternate_history_engine as ah
import alternate_history_branching as br
from alternate_history_maxpf import best_lineup_points
import run_fsffl_multiseason_branch_replay as v1
from run_fsffl_multiseason_branch_replay_v2 import stable_compact_prune
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions, starter_slots, weekly_points_index
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_policy_triage import run as run_triage
from run_fsffl_historical_usage_policy_v3 import run as run_usage_policy
from run_fsffl_historical_trade_policy_v2 import run as run_trade_policy
from run_fsffl_historical_trade_policy_v3 import run as run_trade_expansion

DATA = Path("data")
DEFAULT_MAX_BRANCHES = 256
LEDGER_KEY = "_alternate_history_season_ledger"


def event_season_week(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    meta = event.get("metadata") or {}
    season = event.get("source_season") or event.get("season") or meta.get("source_season") or meta.get("season")
    week = event.get("leg") or event.get("week") or meta.get("leg") or meta.get("week")
    try:
        parsed_week = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed_week = None
    return (str(season) if season is not None else None, parsed_week)


def historical_settings(adapter: FSFFLHistoricalAdapter, season: str) -> Dict[str, Any]:
    for row in adapter.raw_history_seasons():
        league = row.get("league") or {}
        if str(league.get("season") or "") == str(season):
            return dict(league.get("settings") or {})
    if str((adapter.league or {}).get("season") or "") == str(season):
        return dict((adapter.league or {}).get("settings") or {})
    return {}


def apply_preserving_ledger(state_payload: Dict[str, Any], event: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    ledger = copy.deepcopy(state_payload.get(LEDGER_KEY) or {})
    new_state = v1.apply_outcome(state_payload, event, outcome)
    new_state[LEDGER_KEY] = ledger
    return new_state


def score_week(
    branches: List[br.WeightedBranch],
    *,
    season: str,
    week: int,
    slots: List[str],
    positions: Dict[str, str],
    points_by_week: Dict[int, Dict[str, float]],
) -> None:
    realized = points_by_week.get(int(week), {})
    for branch in branches:
        ledger = copy.deepcopy(branch.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {"weekly_max_pf": {}, "season_max_pf": {}})
        weekly = season_row.setdefault("weekly_max_pf", {})
        totals = season_row.setdefault("season_max_pf", {})
        for rid, players in (branch.state.get("roster_players") or {}).items():
            score, lineup = best_lineup_points(players or [], slots, positions, realized)
            weekly.setdefault(str(rid), {})[str(week)] = {"max_pf": score, "lineup": lineup}
            totals[str(rid)] = round(float(totals.get(str(rid)) or 0.0) + float(score), 2)
        branch.state[LEDGER_KEY] = ledger


def run(scenario_path: Path, *, max_branches: int = DEFAULT_MAX_BRANCHES) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    fork_season = str(payload.get("fork_season") or "")
    fork_week = int(payload.get("fork_week") or 1)
    if not fork_season:
        raise ah.AlternateHistoryError("0.7b requires fork_season in the scenario")

    settings = historical_settings(adapter, fork_season)
    playoff_start = int(settings.get("playoff_week_start") or 15)
    matchups = load(DATA / "stats" / "fsffl" / fork_season / "league_matchups_raw.json")
    points_by_week = weekly_points_index(matchups)
    positions = player_positions()
    slots = starter_slots(adapter.league)

    triage = load(run_triage(scenario_path))
    usage = load(run_usage_policy(scenario_path))
    trade = load(run_trade_policy(scenario_path))
    expansion = load(run_trade_expansion(scenario_path))
    usage_by_id = {str(x.get("transaction_id")): x for x in (usage.get("decisions") or [])}
    trade_by_id = {str(x.get("transaction_id")): x for x in (trade.get("decisions") or [])}
    expansion_by_id = {str(x.get("transaction_id")): x for x in (expansion.get("expansions") or [])}
    queues = triage.get("queues") or {}
    required = {str(x) for x in queues.get("required_branch_transaction_ids") or []}
    usage_ids = {str(x) for x in queues.get("historical_usage_policy_transaction_ids") or []}
    trade_ids = {str(x) for x in queues.get("historical_gm_required_transaction_ids") or []}
    stable = {str(x) for x in queues.get("structurally_stable_transaction_ids") or []}

    root_state = ah.apply_fork(ah.reconstruct_state(adapter, scenario.fork_timestamp_ms), scenario)
    root_payload = v1.serial(root_state)
    root_payload[LEDGER_KEY] = {}
    branches = [br.root_branch(root_payload)]
    global_coverage = 1.0
    branch_events: List[Dict[str, Any]] = []
    scored_weeks: List[int] = []
    next_score_week = fork_week
    max_observed_branches = 1

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue
        season, week = event_season_week(event)
        if season == fork_season and week is not None:
            while next_score_week < min(int(week), playoff_start):
                score_week(branches, season=fork_season, week=next_score_week, slots=slots, positions=positions, points_by_week=points_by_week)
                scored_weeks.append(next_score_week)
                next_score_week += 1

        tid = str(event.get("transaction_id") or "")
        if tid in usage_ids:
            policy_row = usage_by_id.get(tid)
            proposed = v1.usage_outcomes(event, policy_row or {}) if policy_row else [{"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}]
            kind = "historical_usage_policy"
        elif tid in trade_ids:
            decision = trade_by_id.get(tid)
            proposed = v1.trade_outcomes(event, decision or {}, expansion_by_id.get(tid)) if decision else [{"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}]
            kind = "historical_trade_policy"
        elif tid in required:
            proposed = [
                {"outcome": "preserve_if_legal", "probability": 1.0, "mode": "exact"},
                {"outcome": "forced_no_action", "probability": 0.0, "mode": "no_action"},
            ]
            kind = "required_branch"
        else:
            proposed = [
                {"outcome": "preserve_historical", "probability": 1.0, "mode": "exact"},
                {"outcome": "legality_forced_no_action", "probability": 0.0, "mode": "no_action"},
            ]
            kind = "structurally_stable" if tid in stable else "invariant"

        expanded: List[br.WeightedBranch] = []
        branch_legality_changed = False
        genuinely_branched = False
        for parent in branches:
            outcomes = v1.branch_specific_outcomes(parent.state, event, proposed)
            if len(outcomes) > 1:
                genuinely_branched = True
            if len(outcomes) != 1 or outcomes[0].get("mode") != "exact":
                branch_legality_changed = True
            parent_traces = parent.traces or [[]]
            for idx, outcome in enumerate(outcomes):
                p = float(parent.probability) * float(outcome.get("probability") or 0.0)
                if p <= 0.0:
                    continue
                state = apply_preserving_ledger(parent.state, event, outcome)
                step = {
                    "transaction_id": tid,
                    "timestamp_ms": created,
                    "kind": kind,
                    "outcome": outcome.get("outcome"),
                    "conditional_probability": round(float(outcome.get("probability") or 0.0), 8),
                }
                if outcome.get("package_id"):
                    step["package_id"] = outcome.get("package_id")
                traces = [(list(t) + [step]) for t in parent_traces[:3]]
                expanded.append(br.WeightedBranch(
                    branch_id=f"{parent.branch_id}/{tid}:{idx}",
                    probability=p,
                    state=state,
                    traces=traces,
                ))

        if not expanded:
            raise ah.AlternateHistoryError(f"0.7b produced zero branches at transaction {tid}")

        if kind == "invariant" and not genuinely_branched and not branch_legality_changed:
            branches = expanded
            continue

        branches, audit = stable_compact_prune(expanded, max_branches)
        global_coverage *= float(audit["retained_fraction_of_incoming_beam"])
        max_observed_branches = max(max_observed_branches, len(branches))
        branch_events.append({
            "transaction_id": tid,
            "timestamp_ms": created,
            "kind": kind,
            "genuinely_branched": genuinely_branched,
            "branch_legality_changed": branch_legality_changed,
            "global_probability_coverage_after_event": global_coverage,
            **audit,
        })

    while next_score_week < playoff_start:
        score_week(branches, season=fork_season, week=next_score_week, slots=slots, positions=positions, points_by_week=points_by_week)
        scored_weeks.append(next_score_week)
        next_score_week += 1

    # Ledger is now part of state identity; convergent rosters with different
    # historical Max PF cannot be merged away.
    branches, final_audit = stable_compact_prune(branches, max_branches)
    global_coverage *= float(final_audit["retained_fraction_of_incoming_beam"])
    conditional_mass = sum(float(x.probability) for x in branches)

    maxpf_distributions: Dict[str, Dict[str, float]] = {}
    for branch in branches:
        totals = (((branch.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("season_max_pf") or {})
        for rid, value in totals.items():
            key = f"{float(value):.2f}"
            maxpf_distributions.setdefault(str(rid), {}).setdefault(key, 0.0)
            maxpf_distributions[str(rid)][key] += float(branch.probability)

    report = {
        "model_version": "Fantasy-Alternate-History-0.7b-stable-fork-season-maxpf",
        "scenario_id": scenario.scenario_id,
        "fork_season": fork_season,
        "playoff_week_start": playoff_start,
        "regular_season_weeks_scored": scored_weeks,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "max_pf_uses_exact_best_ball_lineup_optimizer": True,
            "branch_specific_roster_eligibility": True,
            "season_consequences_part_of_branch_identity_before_merge": True,
            "retained_beam_weights_conditionally_normalized": True,
            "global_probability_coverage_explicit": True,
            "current_gm3_numeric_values_used": False,
            "future_nfl_outcomes_used_for_historical_decisions": False,
        },
        "summary": {
            "terminal_branches": len(branches),
            "final_conditional_probability_mass": round(conditional_mass, 10),
            "global_probability_coverage_retained": round(global_coverage, 10),
            "global_probability_mass_pruned": round(max(0.0, 1.0 - global_coverage), 10),
            "max_observed_retained_branches": max_observed_branches,
        },
        "max_pf_distributions_by_roster": {
            rid: [
                {"max_pf": float(value), "conditional_probability": round(mass, 8)}
                for value, mass in sorted(rows.items(), key=lambda x: float(x[0]))
            ]
            for rid, rows in sorted(maxpf_distributions.items())
        },
        "representative_branches": [
            {
                "branch_id": x.branch_id,
                "conditional_probability": round(float(x.probability), 10),
                "unconditional_probability_approx": round(float(x.probability) * global_coverage, 10),
                "season_max_pf": (((x.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("season_max_pf") or {}),
                "trace": (x.traces or [[]])[0],
            }
            for x in branches[:20]
        ],
        "branch_event_audit": branch_events,
    }
    out = ah.write_isolated_json(f"results/{scenario.scenario_id}/multiseason_branch_replay_0_7b.json", report)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.7b stable fork-season Max PF replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--max-branches", type=int, default=DEFAULT_MAX_BRANCHES)
    args = parser.parse_args()
    run(args.scenario, max_branches=args.max_branches)


if __name__ == "__main__":
    main()
