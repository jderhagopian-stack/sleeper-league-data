#!/usr/bin/env python3
"""FSFFL Alternate History 0.8a: state-aware alternate rookie draft particles.

Builds the 2024 rookie draft directly inside each weighted alternate state:
- prior season finish / Max PF determines the original franchise slot;
- pre-draft transactions determine who controls that original pick at draft time;
- the branch-specific controller makes the selection;
- manager revealed draft preference and prior draft tendencies are historical-safe;
- branch roster composition is recalculated after every selection;
- the actual same-draft board is contemporaneous market evidence only;
- selected rookies are removed from later picks in that same universe.

No future NFL outcomes or current GM 3.0 values are used.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import alternate_history_engine as ah
from alternate_history_rookie_draft_policy import candidate_distribution
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_predraft_particles as predraft
from run_fsffl_alternate_draft_candidates import raw_draft, user_to_roster_for_season
from run_fsffl_alternate_draft_policy import normalized_picks, prior_draft_tendencies
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions
from run_fsffl_downstream_dependencies import load

DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
DRAFT_KEY = "_alternate_history_rookie_draft"
SeasonParticleGroup = season_v3.SeasonParticleGroup


def build_predraft_groups(
    scenario_path: Path,
    *,
    particles: int,
    seed: int,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    groups, boundary_meta = predraft.anchored_boundary_simulate(
        scenario_path,
        particles=particles,
        seed=seed,
    )
    scenario = boundary_meta["scenario"]
    fork_season = str(boundary_meta["fork_season"])
    draft_season = str(int(fork_season) + 1)
    start_ms = predraft.draft_start_ms(draft_season)
    boundary_cutoff = int(boundary_meta.get("cutoff_timestamp_ms") or 0)

    adapter = FSFFLHistoricalAdapter()
    policies = particle_v1.policy_inputs(adapter, scenario, scenario_path)
    triage = policies["triage"]
    usage = policies["usage"]
    trade = policies["trade"]
    expansion = policies["expansion"]
    usage_by_id = {str(x.get("transaction_id")): x for x in (usage.get("decisions") or [])}
    trade_by_id = {str(x.get("transaction_id")): x for x in (trade.get("decisions") or [])}
    expansion_by_id = {str(x.get("transaction_id")): x for x in (expansion.get("expansions") or [])}
    queues = triage.get("queues") or {}
    required = {str(x) for x in queues.get("required_branch_transaction_ids") or []}
    usage_ids = {str(x) for x in queues.get("historical_usage_policy_transaction_ids") or []}
    trade_ids = {str(x) for x in queues.get("historical_gm_required_transaction_ids") or []}
    stable = {str(x) for x in queues.get("structurally_stable_transaction_ids") or []}

    events = [
        event for event in adapter.completed_events()
        if int(event.get("created") or 0) >= boundary_cutoff
        and int(event.get("created") or 0) < start_ms
    ]
    rng = random.Random(seed ^ 0xD4A47)
    event_audit: List[Dict[str, Any]] = []

    for event in events:
        tid = str(event.get("transaction_id") or "")
        kind, proposed = particle_v1.proposed_outcomes(
            event,
            tid,
            usage_ids,
            trade_ids,
            required,
            stable,
            usage_by_id,
            trade_by_id,
            expansion_by_id,
        )
        next_groups: List[SeasonParticleGroup] = []
        actual_branching = False
        legality_changed = False
        for group in groups:
            outcomes = branch_v1.branch_specific_outcomes(group.state, event, proposed)
            if len(outcomes) > 1:
                actual_branching = True
            if len(outcomes) != 1 or outcomes[0].get("mode") != "exact":
                legality_changed = True
            counts = particle_v1.multinomial_counts(
                group.count,
                [float(row.get("probability") or 0.0) for row in outcomes],
                rng,
            )
            if sum(counts) != group.count:
                raise ah.AlternateHistoryError(f"0.8a predraft particle conservation failed at {tid}")
            for idx, (outcome, count) in enumerate(zip(outcomes, counts)):
                if count <= 0:
                    continue
                state = season_v3.apply_preserving_ledger(group.state, event, outcome)
                step = {
                    "transaction_id": tid,
                    "timestamp_ms": int(event.get("created") or 0),
                    "kind": kind,
                    "outcome": outcome.get("outcome"),
                    "conditional_probability": round(float(outcome.get("probability") or 0.0), 8),
                    "particles": count,
                }
                if outcome.get("package_id"):
                    step["package_id"] = outcome.get("package_id")
                traces = [
                    list(trace) + [step]
                    for trace in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]
                ]
                next_groups.append(SeasonParticleGroup(count, state, traces))
        if sum(group.count for group in next_groups) != particles:
            raise ah.AlternateHistoryError(f"0.8a predraft global conservation failed at {tid}")
        if kind == "invariant" and not actual_branching and not legality_changed:
            groups = next_groups
            continue
        groups, merged = season_v3.merge_groups(next_groups)
        event_audit.append({
            "transaction_id": tid,
            "kind": kind,
            "actual_branching": actual_branching,
            "legality_changed": legality_changed,
            "unique_states_after_event": len(groups),
            "particles_in_merged_duplicates": merged,
        })

    groups, _ = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("0.8a predraft final conservation failed")
    return groups, {
        "scenario": scenario,
        "fork_season": fork_season,
        "draft_season": draft_season,
        "draft_start_timestamp_ms": start_ms,
        "predraft_events": len(events),
        "predraft_event_audit": event_audit,
    }


def roster_to_user_for_season(season: str) -> Dict[str, str]:
    return {rid: uid for uid, rid in user_to_roster_for_season(season).items()}


def actual_revealed_by_controller_round(
    picks: List[Dict[str, Any]],
) -> Dict[Tuple[str, int], Set[str]]:
    out: Dict[Tuple[str, int], Set[str]] = defaultdict(set)
    for pick in picks:
        uid = str(pick.get("picked_by_user_id") or "")
        rnd = int(pick.get("round") or 0)
        pid = str(pick.get("player_id") or "")
        if uid and rnd > 0 and pid:
            out[(uid, rnd)].add(pid)
    return out


def drafted_ids(state: Dict[str, Any]) -> Set[str]:
    node = state.get(DRAFT_KEY) or {}
    return {str(x) for x in (node.get("selected_player_ids") or [])}


def apply_draft_pick(
    state: Dict[str, Any],
    *,
    draft_season: str,
    round_no: int,
    slot: int,
    original_roster_id: str,
    controller_roster_id: str,
    controller_user_id: str,
    player: Dict[str, Any],
) -> Dict[str, Any]:
    out = copy.deepcopy(state)
    pid = str(player.get("player_id") or "")
    if not pid:
        raise ah.AlternateHistoryError("0.8a attempted to draft empty player id")
    for players in (out.get("roster_players") or {}).values():
        if isinstance(players, list):
            while pid in players:
                players.remove(pid)
        elif isinstance(players, set):
            players.discard(pid)
    roster_players = out.setdefault("roster_players", {})
    roster = roster_players.setdefault(str(controller_roster_id), [])
    if isinstance(roster, set):
        roster.add(pid)
    elif pid not in roster:
        roster.append(pid)
        roster.sort()

    pick_key = f"pick:{draft_season}:R{int(round_no)}:orig{original_roster_id}"
    (out.setdefault("pick_owners", {})).pop(pick_key, None)

    draft_node = copy.deepcopy(out.get(DRAFT_KEY) or {})
    selected = [str(x) for x in (draft_node.get("selected_player_ids") or [])]
    if pid not in selected:
        selected.append(pid)
    draft_node["selected_player_ids"] = selected
    draft_node.setdefault("picks", []).append({
        "draft_season": str(draft_season),
        "round": int(round_no),
        "slot": int(slot),
        "pick_no": (int(round_no) - 1) * 12 + int(slot),
        "original_roster_id": str(original_roster_id),
        "controller_roster_id": str(controller_roster_id),
        "controller_user_id": str(controller_user_id),
        "player_id": pid,
        "player_name": player.get("player_name"),
        "position": player.get("position"),
        "actual_market_pick_no": int(player.get("pick_no") or 0),
    })
    out[DRAFT_KEY] = draft_node
    return out


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    groups, meta = build_predraft_groups(
        scenario_path,
        particles=particles,
        seed=seed,
    )
    scenario = meta["scenario"]
    fork_season = str(meta["fork_season"])
    draft_season = str(meta["draft_season"])

    entry = raw_draft(draft_season)
    draft = entry.get("draft") or {}
    picks = normalized_picks(entry)
    teams = int((draft.get("settings") or {}).get("teams") or 12)
    rounds = int((draft.get("settings") or {}).get("rounds") or 0)
    if teams != 12 or rounds <= 0:
        raise ah.AlternateHistoryError(f"0.8a unsupported draft settings teams={teams} rounds={rounds}")

    positions = player_positions()
    tendencies = prior_draft_tendencies(int(draft_season))
    roster_to_user = roster_to_user_for_season(draft_season)
    revealed = actual_revealed_by_controller_round(picks)
    by_round: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for pick in picks:
        by_round[int(pick.get("round") or 0)].append(pick)
    for rows in by_round.values():
        rows.sort(key=lambda p: int(p.get("pick_no") or 0))

    rng = random.Random(seed ^ 0x8A08A)
    pick_audit: List[Dict[str, Any]] = []
    max_unique_states = len(groups)

    for rnd in range(1, rounds + 1):
        for slot in range(1, teams + 1):
            current_pick_no = (rnd - 1) * teams + slot
            next_groups: List[SeasonParticleGroup] = []
            controller_counts: Dict[str, int] = defaultdict(int)
            selection_counts: Dict[str, int] = defaultdict(int)

            for group in groups:
                season_row = ((group.state.get(season_v3.LEDGER_KEY) or {}).get(fork_season) or {})
                full_slots = season_row.get("full_following_draft_slots") or {}
                original = next(
                    (str(rid) for rid, value in full_slots.items() if int(value) == int(slot)),
                    None,
                )
                if original is None:
                    raise ah.AlternateHistoryError(f"0.8a no original roster mapped to slot {slot}")
                controller = predraft.controller_for(group.state, draft_season, rnd, original)
                controller_uid = str(roster_to_user.get(controller) or "")
                controller_counts[controller] += group.count

                unavailable = drafted_ids(group.state)
                available = [
                    player for player in by_round.get(rnd, [])
                    if str(player.get("player_id")) not in unavailable
                ]
                dist = candidate_distribution(
                    available,
                    current_pick_no=current_pick_no,
                    controller_roster_id=controller,
                    controller_user_id=controller_uid,
                    revealed_player_ids=revealed.get((controller_uid, rnd), set()),
                    tendencies=tendencies,
                    state=group.state,
                    positions=positions,
                )
                if not dist:
                    raise ah.AlternateHistoryError(f"0.8a empty candidate distribution at pick {current_pick_no}")

                counts = particle_v1.multinomial_counts(
                    group.count,
                    [float(row.get("probability") or 0.0) for row in dist],
                    rng,
                )
                if sum(counts) != group.count:
                    raise ah.AlternateHistoryError(f"0.8a draft particle conservation failed at pick {current_pick_no}")
                for idx, (row, count) in enumerate(zip(dist, counts)):
                    if count <= 0:
                        continue
                    player = row["player"]
                    pid = str(player.get("player_id"))
                    selection_counts[pid] += count
                    state = apply_draft_pick(
                        group.state,
                        draft_season=draft_season,
                        round_no=rnd,
                        slot=slot,
                        original_roster_id=original,
                        controller_roster_id=controller,
                        controller_user_id=controller_uid,
                        player=player,
                    )
                    step = {
                        "kind": "alternate_rookie_draft_pick",
                        "draft_season": draft_season,
                        "round": rnd,
                        "slot": slot,
                        "pick_no": current_pick_no,
                        "original_roster_id": original,
                        "controller_roster_id": controller,
                        "player_id": pid,
                        "player_name": player.get("player_name"),
                        "conditional_probability": round(float(row.get("probability") or 0.0), 8),
                        "particles": count,
                    }
                    traces = [
                        list(trace) + [step]
                        for trace in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]
                    ]
                    next_groups.append(SeasonParticleGroup(count, state, traces))

            if sum(group.count for group in next_groups) != particles:
                raise ah.AlternateHistoryError(f"0.8a global conservation failed at draft pick {current_pick_no}")
            groups, merged = season_v3.merge_groups(next_groups)
            max_unique_states = max(max_unique_states, len(groups))
            player_by_id = {str(p.get("player_id")): p for p in picks}
            pick_audit.append({
                "round": rnd,
                "slot": slot,
                "pick_no": current_pick_no,
                "unique_states_after_pick": len(groups),
                "particles_in_merged_duplicates": merged,
                "controller_distribution": [
                    {"roster_id": rid, "particles": count, "probability": round(count / particles, 8)}
                    for rid, count in sorted(controller_counts.items(), key=lambda row: (-row[1], row[0]))
                ],
                "selection_distribution": [
                    {
                        "player_id": pid,
                        "player_name": (player_by_id.get(pid) or {}).get("player_name"),
                        "position": (player_by_id.get(pid) or {}).get("position"),
                        "particles": count,
                        "probability": round(count / particles, 8),
                    }
                    for pid, count in sorted(selection_counts.items(), key=lambda row: (-row[1], row[0]))
                ],
            })

    groups, final_merged = season_v3.merge_groups(groups)
    final_particles = sum(group.count for group in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(f"0.8a final particle conservation failed {final_particles} != {particles}")

    focus = str(scenario.focus_roster_id)
    focus_player_counts: Dict[str, int] = defaultdict(int)
    focus_pick_counts: Dict[str, int] = defaultdict(int)
    for group in groups:
        draft_node = group.state.get(DRAFT_KEY) or {}
        for pick in draft_node.get("picks") or []:
            if str(pick.get("controller_roster_id")) != focus:
                continue
            pid = str(pick.get("player_id"))
            focus_player_counts[pid] += group.count
            label = f"R{int(pick.get('round') or 0)}:slot{int(pick.get('slot') or 0)}:orig{pick.get('original_roster_id')}"
            focus_pick_counts[label] += group.count

    player_by_id = {str(p.get("player_id")): p for p in picks}
    report = {
        "model_version": "Fantasy-Alternate-History-0.8a-state-aware-rookie-draft-particles",
        "scenario_id": scenario.scenario_id,
        "draft_season": draft_season,
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "completed_nfl_outcomes_used": False,
            "current_gm3_numeric_values_used": False,
            "actual_same_draft_order_is_contemporaneous_market_evidence": True,
            "prior_manager_draft_tendencies_only": True,
            "branch_roster_need_recomputed_after_every_pick": True,
            "original_roster_determines_slot": True,
            "branch_specific_pick_controller_makes_selection": True,
            "selected_rookie_removed_from_later_branch_picks": True,
            "particle_probability_mass_pruned": False,
        },
        "summary": {
            "predraft_events_replayed": meta["predraft_events"],
            "draft_picks_simulated": rounds * teams,
            "final_particles": final_particles,
            "final_probability_mass": 1.0,
            "final_unique_postdraft_states": len(groups),
            "max_unique_states": max_unique_states,
            "final_particles_merged": final_merged,
        },
        "focus_roster_id": focus,
        "focus_player_acquisition_probabilities": [
            {
                "player_id": pid,
                "player_name": (player_by_id.get(pid) or {}).get("player_name"),
                "position": (player_by_id.get(pid) or {}).get("position"),
                "particles_acquired": count,
                "probability_roster_drafts_player": round(count / particles, 8),
            }
            for pid, count in sorted(focus_player_counts.items(), key=lambda row: (-row[1], row[0]))
        ],
        "focus_controlled_pick_frequency": [
            {"pick": label, "particles": count, "probability": round(count / particles, 8)}
            for label, count in sorted(focus_pick_counts.items())
        ],
        "pick_audit": pick_audit,
        "predraft_event_audit": meta["predraft_event_audit"],
        "representative_postdraft_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted((group.state.get("roster_players") or {}).get(focus, [])),
                "alternate_draft": group.state.get(DRAFT_KEY) or {},
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda x: x.count, reverse=True)[:20]
        ],
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/alternate_rookie_draft_particles_0_8a.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run state-aware alternate rookie draft particles")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
