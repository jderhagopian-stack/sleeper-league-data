#!/usr/bin/env python3
"""FSFFL Alternate History 0.8c: stateful 2025 rookie-draft handoff.

Consumes the complete weighted particle state produced at the end of the 2024
alternate-history season and replays the 2025 rookie draft inside each state.
This is a dependency stage: it advances the branch state without introducing
future NFL information, current GM 3.0 values, or probability-mass pruning.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import alternate_history_engine as ah
from alternate_history_rookie_draft_policy import candidate_distribution
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_alternate_rookie_draft_particles as draft_runner
import run_fsffl_second_season_particles as second_season
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_alternate_draft_policy import normalized_picks, prior_draft_tendencies
from run_fsffl_counterfactual_replay import player_positions

DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
SeasonParticleGroup = season_v3.SeasonParticleGroup


def replay_rookie_draft_groups(
    groups: List[SeasonParticleGroup],
    *,
    completed_season: str,
    draft_season: str,
    particles: int,
    seed: int,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    """Replay one historical rookie draft from an already-divergent state set."""
    entry = raw_draft(draft_season)
    draft = entry.get("draft") or {}
    picks = normalized_picks(entry)
    teams = int((draft.get("settings") or {}).get("teams") or 0)
    rounds = int((draft.get("settings") or {}).get("rounds") or 0)
    if teams <= 0 or rounds <= 0:
        raise ah.AlternateHistoryError(
            f"0.8c invalid draft settings teams={teams} rounds={rounds}"
        )
    if teams != 12:
        raise ah.AlternateHistoryError(
            "0.8c FSFFL adapter currently expects the league's historical 12-team draft"
        )

    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("0.8c input particle mass does not match configuration")

    positions = player_positions()
    tendencies = prior_draft_tendencies(int(draft_season))
    roster_to_user = draft_runner.roster_to_user_for_season(draft_season)
    revealed = draft_runner.actual_revealed_by_controller_round(picks)
    by_round: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for pick in picks:
        by_round[int(pick.get("round") or 0)].append(pick)
    for rows in by_round.values():
        rows.sort(key=lambda p: int(p.get("pick_no") or 0))

    rng = random.Random(seed ^ 0x2025C)
    pick_audit: List[Dict[str, Any]] = []
    max_unique_states = len(groups)

    for rnd in range(1, rounds + 1):
        for slot in range(1, teams + 1):
            current_pick_no = (rnd - 1) * teams + slot
            next_groups: List[SeasonParticleGroup] = []
            controller_counts: Dict[str, int] = defaultdict(int)
            selection_counts: Dict[str, int] = defaultdict(int)

            for group in groups:
                season_row = (
                    (group.state.get(season_v3.LEDGER_KEY) or {}).get(str(completed_season)) or {}
                )
                full_slots = season_row.get("full_following_draft_slots") or {}
                original = next(
                    (str(rid) for rid, value in full_slots.items() if int(value) == int(slot)),
                    None,
                )
                if original is None:
                    raise ah.AlternateHistoryError(
                        f"0.8c has no original franchise mapped to draft slot {slot}"
                    )

                controller = draft_runner.predraft.controller_for(
                    group.state, draft_season, rnd, original
                )
                controller_uid = str(roster_to_user.get(controller) or "")
                controller_counts[controller] += group.count

                unavailable: Set[str] = draft_runner.drafted_ids(group.state)
                available = [
                    player
                    for player in by_round.get(rnd, [])
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
                    raise ah.AlternateHistoryError(
                        f"0.8c empty candidate distribution at pick {current_pick_no}"
                    )

                counts = particle_v1.multinomial_counts(
                    group.count,
                    [float(row.get("probability") or 0.0) for row in dist],
                    rng,
                )
                if sum(counts) != group.count:
                    raise ah.AlternateHistoryError(
                        f"0.8c particle conservation failed at pick {current_pick_no}"
                    )

                for row, count in zip(dist, counts):
                    if count <= 0:
                        continue
                    player = row["player"]
                    pid = str(player.get("player_id") or "")
                    selection_counts[pid] += count
                    state = draft_runner.apply_draft_pick(
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
                raise ah.AlternateHistoryError(
                    f"0.8c global particle conservation failed at pick {current_pick_no}"
                )
            groups, merged = season_v3.merge_groups(next_groups)
            max_unique_states = max(max_unique_states, len(groups))
            pick_audit.append({
                "round": rnd,
                "slot": slot,
                "pick_no": current_pick_no,
                "unique_states_after_pick": len(groups),
                "particles_in_merged_duplicates": merged,
                "controller_counts": dict(controller_counts),
                "selection_counts": dict(selection_counts),
            })

    groups, final_merged = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("0.8c final particle conservation failed")

    return groups, {
        "completed_season": str(completed_season),
        "draft_season": str(draft_season),
        "draft_start_timestamp_ms": int(draft.get("start_time") or draft.get("created") or 0),
        "draft_picks_simulated": teams * rounds,
        "draft_pick_audit": pick_audit,
        "max_unique_states": max_unique_states,
        "final_particles_merged": final_merged,
        "final_unique_states": len(groups),
    }


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.8c particles must be positive")

    _, groups, handoff = second_season.run(
        scenario_path,
        particles=particles,
        seed=seed,
        return_handoff=True,
    )
    scenario = handoff["scenario"]
    completed_season = str(handoff["completed_season"])
    draft_season = str(handoff["next_draft_season"])

    groups, draft_meta = replay_rookie_draft_groups(
        groups,
        completed_season=completed_season,
        draft_season=draft_season,
        particles=particles,
        seed=seed,
    )

    focus = str(scenario.focus_roster_id)
    roster_counts: Dict[str, int] = defaultdict(int)
    focus_pick_counts: Dict[str, int] = defaultdict(int)
    for group in groups:
        focus_players = tuple(sorted(
            str(x) for x in ((group.state.get("roster_players") or {}).get(focus) or [])
        ))
        roster_counts["|".join(focus_players)] += group.count
        draft_node = group.state.get(draft_runner.DRAFT_KEY) or {}
        selected = [
            row for row in (draft_node.get("picks") or [])
            if str(row.get("draft_season")) == draft_season
            and str(row.get("controller_roster_id")) == focus
        ]
        signature = "|".join(
            f"{int(row.get('round') or 0)}.{int(row.get('slot') or 0)}:{row.get('player_id')}"
            for row in sorted(selected, key=lambda value: (int(value.get("round") or 0), int(value.get("slot") or 0)))
        )
        focus_pick_counts[signature] += group.count

    report = {
        "model_version": "Fantasy-Alternate-History-0.8c-next-draft-handoff",
        "scenario_id": scenario.scenario_id,
        "completed_season": completed_season,
        "draft_season": draft_season,
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "completed_nfl_outcomes_are_immutable": True,
            "historical_same_draft_market_only": True,
            "future_nfl_outcomes_used_for_draft_decisions": False,
            "current_gm3_numeric_values_used": False,
            "particle_probability_mass_pruned": False,
            "branch_specific_pick_ownership_used": True,
            "branch_specific_roster_need_used": True,
        },
        "summary": {
            "input_particles": particles,
            "final_particles": sum(group.count for group in groups),
            "final_probability_mass": 1.0,
            "draft_picks_simulated": draft_meta["draft_picks_simulated"],
            "final_unique_postdraft_states": draft_meta["final_unique_states"],
            "max_unique_states": draft_meta["max_unique_states"],
            "final_particles_merged": draft_meta["final_particles_merged"],
        },
        "focus_2025_draft_outcome_distribution": [
            {
                "signature": sig,
                "particles": count,
                "probability": round(count / particles, 8),
            }
            for sig, count in sorted(focus_pick_counts.items(), key=lambda row: (-row[1], row[0]))[:50]
        ],
        "focus_postdraft_roster_distribution": [
            {
                "player_ids": sig.split("|") if sig else [],
                "particles": count,
                "probability": round(count / particles, 8),
            }
            for sig, count in sorted(roster_counts.items(), key=lambda row: (-row[1], row[0]))[:50]
        ],
        "draft_pick_audit": draft_meta["draft_pick_audit"],
        "representative_postdraft_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted(
                    (group.state.get("roster_players") or {}).get(focus, [])
                ),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda value: value.count, reverse=True)[:20]
        ],
    }

    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/next_draft_handoff_0_8c.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the 2025 alternate rookie draft handoff")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
