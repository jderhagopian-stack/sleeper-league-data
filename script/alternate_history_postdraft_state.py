#!/usr/bin/env python3
"""Reusable post-rookie-draft particle state handoff.

Builds branch states through the following rookie draft and returns the actual
post-draft particle groups for continued multi-season propagation. This extracts
the stateful draft mechanics from the reporting runner so later seasons can
reuse one implementation rather than duplicating draft logic.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

import alternate_history_engine as ah
from alternate_history_rookie_draft_policy import candidate_distribution
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_alternate_rookie_draft_particles as draft_runner
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_alternate_draft_policy import normalized_picks, prior_draft_tendencies
from run_fsffl_counterfactual_replay import player_positions

SeasonParticleGroup = season_v3.SeasonParticleGroup
MAX_TRACES_PER_GROUP = 3


def simulate_postdraft_groups(
    scenario_path,
    *,
    particles: int,
    seed: int,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    groups, meta = draft_runner.build_predraft_groups(
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
        raise ah.AlternateHistoryError(
            f"Postdraft handoff unsupported draft settings teams={teams} rounds={rounds}"
        )

    positions = player_positions()
    tendencies = prior_draft_tendencies(int(draft_season))
    roster_to_user = draft_runner.roster_to_user_for_season(draft_season)
    revealed = draft_runner.actual_revealed_by_controller_round(picks)
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
                season_row = (
                    (group.state.get(season_v3.LEDGER_KEY) or {}).get(fork_season) or {}
                )
                full_slots = season_row.get("full_following_draft_slots") or {}
                original = next(
                    (str(rid) for rid, value in full_slots.items() if int(value) == int(slot)),
                    None,
                )
                if original is None:
                    raise ah.AlternateHistoryError(
                        f"Postdraft handoff has no original roster mapped to slot {slot}"
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
                        f"Postdraft handoff empty candidate distribution at pick {current_pick_no}"
                    )

                counts = particle_v1.multinomial_counts(
                    group.count,
                    [float(row.get("probability") or 0.0) for row in dist],
                    rng,
                )
                if sum(counts) != group.count:
                    raise ah.AlternateHistoryError(
                        f"Postdraft particle conservation failed at pick {current_pick_no}"
                    )

                for row, count in zip(dist, counts):
                    if count <= 0:
                        continue
                    player = row["player"]
                    pid = str(player.get("player_id"))
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
                        "conditional_probability": round(
                            float(row.get("probability") or 0.0), 8
                        ),
                        "particles": count,
                    }
                    traces = [
                        list(trace) + [step]
                        for trace in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]
                    ]
                    next_groups.append(SeasonParticleGroup(count, state, traces))

            if sum(group.count for group in next_groups) != particles:
                raise ah.AlternateHistoryError(
                    f"Postdraft global conservation failed at pick {current_pick_no}"
                )
            groups, merged = season_v3.merge_groups(next_groups)
            max_unique_states = max(max_unique_states, len(groups))
            pick_audit.append(
                {
                    "round": rnd,
                    "slot": slot,
                    "pick_no": current_pick_no,
                    "unique_states_after_pick": len(groups),
                    "particles_in_merged_duplicates": merged,
                    "controller_counts": dict(controller_counts),
                    "selection_counts": dict(selection_counts),
                }
            )

    groups, final_merged = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("Postdraft final particle conservation failed")

    return groups, {
        **meta,
        "draft_season": draft_season,
        "draft_end_state_timestamp_ms": int(
            (entry.get("draft") or {}).get("start_time")
            or (entry.get("draft") or {}).get("created")
            or 0
        ),
        "draft_picks_simulated": teams * rounds,
        "draft_pick_audit": pick_audit,
        "max_unique_postdraft_states": max_unique_states,
        "final_draft_merge_particles": final_merged,
        "final_unique_postdraft_states": len(groups),
    }
