#!/usr/bin/env python3
"""FSFFL Alternate History 0.7f: pre-draft replay + branch-specific rookie draft.

Consumes complete 0.7e season-boundary particles, replays any following-season
transactions that occurred BEFORE the historical rookie draft, then conducts a
coupled alternate rookie draft for every particle.

Causal guarantees:
- draft slot is determined by the ORIGINAL roster's alternate season result;
- the controller of that pick is the branch-specific pick owner at draft time;
- pre-draft trades/waivers are replayed before the draft rather than skipped;
- player availability is coupled across the full draft board;
- branch-specific roster composition informs position need;
- only contemporaneous actual draft order, prior draft tendencies, and revealed
  historical manager choices inform selection probabilities;
- no future NFL outcomes or current GM3 values are used.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import alternate_history_engine as ah
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from run_fsffl_alternate_draft_candidates import raw_draft, user_to_roster_for_season
from run_fsffl_alternate_draft_policy import (
    candidate_score,
    league_position_medians,
    normalized_picks,
    player_positions,
    prior_draft_tendencies,
    softmax_choice,
)
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_season_boundary_particles import simulate as simulate_boundary

DATA = Path("data")
DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
DRAFT_LEDGER_KEY = "_alternate_history_draft_ledger"
SEASON_LEDGER_KEY = season_v3.LEDGER_KEY
MAX_TRACES_PER_GROUP = 3
MARKET_RADIUS = 4


def draft_timestamp(entry: Dict[str, Any]) -> int:
    draft = entry.get("draft") or {}
    value = draft.get("start_time") or draft.get("created")
    if value is None:
        raise ah.AlternateHistoryError("Historical rookie draft has no start_time/created timestamp")
    return int(value)


def roster_to_user_for_season(season: str) -> Dict[str, str]:
    return {str(rid): str(uid) for uid, rid in user_to_roster_for_season(str(season)).items()}


def actual_slot_by_original_roster(entry: Dict[str, Any], season: str) -> Dict[str, int]:
    draft = entry.get("draft") or {}
    user_to_roster = user_to_roster_for_season(str(season))
    out: Dict[str, int] = {}
    for uid, slot in (draft.get("draft_order") or {}).items():
        rid = user_to_roster.get(str(uid))
        if rid is not None:
            out[str(rid)] = int(slot)
    return out


def pick_asset_key(season: str, round_no: int, original_roster_id: str) -> str:
    return f"pick:{season}:R{int(round_no)}:orig{original_roster_id}"


def branch_roster_counts(
    state: Dict[str, Any],
    positions: Dict[str, str],
    roster_to_user: Dict[str, str],
) -> Dict[str, Counter]:
    out: Dict[str, Counter] = defaultdict(Counter)
    for rid, players in (state.get("roster_players") or {}).items():
        uid = roster_to_user.get(str(rid))
        if not uid:
            continue
        for pid in players or []:
            pos = positions.get(str(pid), "")
            if pos:
                out[uid][pos] += 1
    return out


def revealed_selection_for_controller(
    controller_uid: str,
    *,
    round_no: int,
    current_pick_no: int,
    source_pick: Dict[str, Any],
    actual_picks: List[Dict[str, Any]],
) -> str:
    if str(source_pick.get("picked_by_user_id") or "") == str(controller_uid):
        return str(source_pick.get("player_id") or "")
    rows = [
        p for p in actual_picks
        if int(p.get("round") or 0) == int(round_no)
        and str(p.get("picked_by_user_id") or "") == str(controller_uid)
    ]
    if not rows:
        return ""
    rows.sort(key=lambda p: (abs(int(p.get("pick_no") or 0) - int(current_pick_no)), int(p.get("pick_no") or 0)))
    return str(rows[0].get("player_id") or "")


def add_drafted_player(state: Dict[str, Any], controller_rid: str, player_id: str) -> None:
    pid = str(player_id)
    for players in (state.get("roster_players") or {}).values():
        if isinstance(players, list):
            while pid in players:
                players.remove(pid)
        elif isinstance(players, set):
            players.discard(pid)
    roster_players = state.setdefault("roster_players", {}).setdefault(str(controller_rid), [])
    if isinstance(roster_players, set):
        roster_players.add(pid)
    elif pid not in roster_players:
        roster_players.append(pid)


def draft_state_key(state: Dict[str, Any]) -> str:
    canonical = {
        "roster_players": {
            str(k): sorted(str(x) for x in (v or []))
            for k, v in sorted((state.get("roster_players") or {}).items())
        },
        "pick_owners": dict(sorted((state.get("pick_owners") or {}).items())),
        "faab": {str(k): float(v or 0.0) for k, v in sorted((state.get("faab") or {}).items())},
        "season_ledger": state.get(SEASON_LEDGER_KEY) or {},
        "draft_ledger": state.get(DRAFT_LEDGER_KEY) or {},
    }
    return ah.stable_hash(canonical)


def merge_draft_groups(groups: Iterable[season_v3.SeasonParticleGroup]) -> Tuple[List[season_v3.SeasonParticleGroup], int]:
    by_key: Dict[str, season_v3.SeasonParticleGroup] = {}
    merged_particles = 0
    for group in groups:
        if group.count <= 0:
            continue
        key = draft_state_key(group.state)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = season_v3.SeasonParticleGroup(
                group.count,
                group.state,
                [list(t) for t in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]],
            )
            continue
        merged_particles += group.count
        existing.count += group.count
        for trace in group.traces or []:
            if len(existing.traces) >= MAX_TRACES_PER_GROUP:
                break
            if trace not in existing.traces:
                existing.traces.append(list(trace))
    return list(by_key.values()), merged_particles


def replay_predraft_events(
    groups: List[season_v3.SeasonParticleGroup],
    *,
    adapter: FSFFLHistoricalAdapter,
    scenario_path: Path,
    start_ms: Optional[int],
    end_ms: int,
    particles: int,
    rng: random.Random,
) -> Tuple[List[season_v3.SeasonParticleGroup], Dict[str, Any]]:
    policies = particle_v1.policy_inputs(adapter, ah.scenario_from_json(adapter, load(scenario_path)), scenario_path)
    triage, usage, trade, expansion = policies["triage"], policies["usage"], policies["trade"], policies["expansion"]
    usage_by_id = {str(x.get("transaction_id")): x for x in (usage.get("decisions") or [])}
    trade_by_id = {str(x.get("transaction_id")): x for x in (trade.get("decisions") or [])}
    expansion_by_id = {str(x.get("transaction_id")): x for x in (expansion.get("expansions") or [])}
    queues = triage.get("queues") or {}
    required = {str(x) for x in queues.get("required_branch_transaction_ids") or []}
    usage_ids = {str(x) for x in queues.get("historical_usage_policy_transaction_ids") or []}
    trade_ids = {str(x) for x in queues.get("historical_gm_required_transaction_ids") or []}
    stable = {str(x) for x in queues.get("structurally_stable_transaction_ids") or []}

    events = [
        e for e in adapter.completed_events()
        if (start_ms is None or int(e.get("created") or 0) >= int(start_ms))
        and int(e.get("created") or 0) < int(end_ms)
    ]
    audits = []
    for event in events:
        tid = str(event.get("transaction_id") or "")
        kind, proposed = particle_v1.proposed_outcomes(
            event, tid, usage_ids, trade_ids, required, stable,
            usage_by_id, trade_by_id, expansion_by_id,
        )
        next_groups: List[season_v3.SeasonParticleGroup] = []
        actual_branching = False
        legality_changed = False
        for group in groups:
            outcomes = branch_v1.branch_specific_outcomes(group.state, event, proposed)
            group_branching = len(outcomes) > 1
            group_legality_changed = len(outcomes) != 1 or outcomes[0].get("mode") != "exact"
            actual_branching = actual_branching or group_branching
            legality_changed = legality_changed or group_legality_changed
            counts = particle_v1.multinomial_counts(
                group.count,
                [float(x.get("probability") or 0.0) for x in outcomes],
                rng,
            )
            if sum(counts) != group.count:
                raise ah.AlternateHistoryError(f"0.7f pre-draft particle conservation failed at {tid}")
            record_trace = kind != "invariant" or group_branching or group_legality_changed
            for outcome, count in zip(outcomes, counts):
                if count <= 0:
                    continue
                state = season_v3.apply_preserving_ledger(group.state, event, outcome)
                if record_trace:
                    step = {
                        "transaction_id": tid,
                        "timestamp_ms": int(event.get("created") or 0),
                        "kind": f"predraft_{kind}",
                        "outcome": outcome.get("outcome"),
                        "particles": count,
                    }
                    traces = [list(t) + [step] for t in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]]
                else:
                    traces = group.traces
                next_groups.append(season_v3.SeasonParticleGroup(count, state, traces))
        if sum(x.count for x in next_groups) != particles:
            raise ah.AlternateHistoryError(f"0.7f pre-draft global particle conservation failed at {tid}")
        groups, merged = merge_draft_groups(next_groups)
        audits.append({
            "transaction_id": tid,
            "timestamp_ms": int(event.get("created") or 0),
            "kind": kind,
            "actual_branching": actual_branching,
            "legality_changed": legality_changed,
            "unique_states_after_event": len(groups),
            "particles_in_merged_duplicates": merged,
        })
    return groups, {"events": len(events), "audit": audits}


def simulate_one_draft(
    state: Dict[str, Any],
    *,
    draft_season: str,
    entry: Dict[str, Any],
    rng: random.Random,
    positions: Dict[str, str],
    tendencies: Dict[str, Counter],
    roster_to_user: Dict[str, str],
    actual_slots: Dict[str, int],
    actual_picks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    out = copy.deepcopy(state)
    season_row = ((out.get(SEASON_LEDGER_KEY) or {}).get(str(int(draft_season) - 1)) or {})
    alt_slots = {str(rid): int(slot) for rid, slot in (season_row.get("full_following_draft_slots") or {}).items()}
    if len(alt_slots) != 12 or sorted(alt_slots.values()) != list(range(1, 13)):
        raise ah.AlternateHistoryError("0.7f requires a complete branch-specific 1-12 draft-slot map")
    orig_by_alt_slot = {slot: rid for rid, slot in alt_slots.items()}
    if len(orig_by_alt_slot) != 12:
        raise ah.AlternateHistoryError("0.7f alternate slot mapping is not one-to-one")

    draft = entry.get("draft") or {}
    teams = int((draft.get("settings") or {}).get("teams") or 12)
    rounds = int((draft.get("settings") or {}).get("rounds") or 0)
    if teams != 12 or rounds <= 0:
        raise ah.AlternateHistoryError("0.7f historical draft settings unavailable/unsupported")

    by_round_slot = {(int(p["round"]), int(p["draft_slot"])): p for p in actual_picks}
    roster_counts = branch_roster_counts(out, positions, roster_to_user)
    medians = league_position_medians(roster_counts)
    drafted: set[str] = set()
    selections: List[Dict[str, Any]] = []

    for rnd in range(1, rounds + 1):
        for alt_slot in range(1, teams + 1):
            pick_no = (rnd - 1) * teams + alt_slot
            orig_rid = str(orig_by_alt_slot[alt_slot])
            source_actual_slot = int(actual_slots[orig_rid])
            source_pick = by_round_slot.get((rnd, source_actual_slot)) or {}
            asset_key = pick_asset_key(draft_season, rnd, orig_rid)
            controller_rid = str((out.get("pick_owners") or {}).get(asset_key) or orig_rid)
            controller_uid = roster_to_user.get(controller_rid, "")
            if not controller_uid:
                raise ah.AlternateHistoryError(
                    f"0.7f cannot resolve controller user for roster {controller_rid}"
                )

            revealed = revealed_selection_for_controller(
                controller_uid,
                round_no=rnd,
                current_pick_no=pick_no,
                source_pick=source_pick,
                actual_picks=actual_picks,
            )
            available = [p for p in actual_picks if str(p.get("player_id")) not in drafted]
            local = [p for p in available if abs(int(p.get("pick_no") or 0) - pick_no) <= MARKET_RADIUS]
            if revealed and revealed not in drafted:
                row = next((p for p in available if str(p.get("player_id")) == revealed), None)
                if row is not None and row not in local:
                    local.append(row)
            if not local:
                available.sort(key=lambda p: abs(int(p.get("pick_no") or 0) - pick_no))
                local = available[: min(8, len(available))]
            if not local:
                raise ah.AlternateHistoryError(f"0.7f no candidates at pick {pick_no}")

            scored = [
                (
                    candidate_score(
                        p,
                        pick_no,
                        controller_uid,
                        revealed,
                        tendencies,
                        roster_counts,
                        medians,
                    ),
                    p,
                )
                for p in local
            ]
            chosen = softmax_choice(rng, scored)
            pid = str(chosen.get("player_id") or "")
            if not pid or pid in drafted:
                raise ah.AlternateHistoryError(f"0.7f invalid duplicate/empty draft choice at pick {pick_no}")
            drafted.add(pid)
            add_drafted_player(out, controller_rid, pid)
            if chosen.get("position"):
                roster_counts.setdefault(controller_uid, Counter())[str(chosen.get("position"))] += 1
            (out.get("pick_owners") or {}).pop(asset_key, None)
            selections.append({
                "pick_no": pick_no,
                "round": rnd,
                "alternate_slot": alt_slot,
                "original_roster_id": orig_rid,
                "source_actual_slot": source_actual_slot,
                "pick_asset_key": asset_key,
                "controller_roster_id": controller_rid,
                "controller_user_id": controller_uid,
                "player_id": pid,
                "player_name": chosen.get("player_name"),
                "position": chosen.get("position"),
                "revealed_preference_player_id": revealed or None,
            })

    out.setdefault(DRAFT_LEDGER_KEY, {})[str(draft_season)] = {
        "draft_timestamp_ms": draft_timestamp(entry),
        "selections": selections,
    }
    return out


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    boundary_groups, boundary_meta = simulate_boundary(
        scenario_path, particles=particles, seed=seed
    )
    scenario = boundary_meta["scenario"]
    fork_season = str(boundary_meta["fork_season"])
    draft_season = str(int(fork_season) + 1)
    adapter = FSFFLHistoricalAdapter()
    entry = raw_draft(draft_season)
    start_ms = draft_timestamp(entry)
    boundary_cutoff = boundary_meta.get("cutoff_timestamp_ms")
    rng = random.Random(int(seed) ^ 0xDFAF7)

    groups, predraft = replay_predraft_events(
        boundary_groups,
        adapter=adapter,
        scenario_path=scenario_path,
        start_ms=boundary_cutoff,
        end_ms=start_ms,
        particles=particles,
        rng=rng,
    )

    positions = player_positions()
    tendencies = prior_draft_tendencies(int(draft_season))
    roster_to_user = roster_to_user_for_season(draft_season)
    actual_slots = actual_slot_by_original_roster(entry, draft_season)
    actual_picks = normalized_picks(entry)

    drafted_groups: List[season_v3.SeasonParticleGroup] = []
    for group in groups:
        for _ in range(group.count):
            state = simulate_one_draft(
                group.state,
                draft_season=draft_season,
                entry=entry,
                rng=rng,
                positions=positions,
                tendencies=tendencies,
                roster_to_user=roster_to_user,
                actual_slots=actual_slots,
                actual_picks=actual_picks,
            )
            step = {
                "kind": "alternate_rookie_draft",
                "draft_season": draft_season,
                "draft_timestamp_ms": start_ms,
            }
            traces = [list(t) + [step] for t in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]]
            drafted_groups.append(season_v3.SeasonParticleGroup(1, state, traces))

    groups, merged = merge_draft_groups(drafted_groups)
    final_particles = sum(x.count for x in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(
            f"0.7f draft particle conservation failed: {final_particles} != {particles}"
        )

    focus = str(scenario.focus_roster_id)
    focus_selection_counts: Dict[Tuple[int, str], int] = defaultdict(int)
    controller_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for group in groups:
        draft_row = (group.state.get(DRAFT_LEDGER_KEY) or {}).get(draft_season) or {}
        for pick in draft_row.get("selections") or []:
            if str(pick.get("controller_roster_id")) == focus:
                key = (int(pick.get("round") or 0), str(pick.get("player_id") or ""))
                focus_selection_counts[key] += group.count
            asset = str(pick.get("pick_asset_key") or "")
            controller = str(pick.get("controller_roster_id") or "")
            if asset and controller:
                controller_counts[asset][controller] += group.count

    player_by_id = {str(p.get("player_id")): p for p in actual_picks}
    focus_distributions = []
    rounds = int(((entry.get("draft") or {}).get("settings") or {}).get("rounds") or 0)
    for rnd in range(1, rounds + 1):
        rows = []
        for (r, pid), count in focus_selection_counts.items():
            if r != rnd:
                continue
            p = player_by_id.get(pid) or {}
            rows.append({
                "player_id": pid,
                "player_name": p.get("player_name") or pid,
                "position": p.get("position"),
                "particles": count,
                "probability": round(count / particles, 8),
            })
        rows.sort(key=lambda x: (-x["probability"], x["player_id"]))
        focus_distributions.append({"round": rnd, "selections": rows})

    report = {
        "model_version": "Fantasy-Alternate-History-0.7f-predraft-and-rookie-draft-particles",
        "scenario_id": scenario.scenario_id,
        "draft_season": draft_season,
        "configuration": {"particles": particles, "seed": seed, "market_radius": MARKET_RADIUS},
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "pre_draft_following_season_transactions_replayed": True,
            "pick_slot_follows_original_roster_result": True,
            "pick_controller_uses_branch_specific_ownership_at_draft": True,
            "draft_board_coupled_player_selected_once": True,
            "branch_roster_composition_used_for_need": True,
            "future_nfl_outcomes_used": False,
            "current_gm3_numeric_values_used": False,
            "particle_probability_mass_pruned": False,
        },
        "summary": {
            "final_particles": final_particles,
            "final_probability_mass": round(final_particles / particles, 10),
            "boundary_unique_states": len(boundary_groups),
            "predraft_events_replayed": predraft["events"],
            "unique_states_after_predraft": len(groups) if not drafted_groups else None,
            "final_unique_postdraft_states": len(groups),
            "draft_timestamp_ms": start_ms,
            "season_boundary_cutoff_timestamp_ms": boundary_cutoff,
            "particles_merged_after_draft": merged,
        },
        "focus_draft_selection_distributions": focus_distributions,
        "pick_controller_probabilities_at_selection": {
            asset: [
                {"roster_id": rid, "particles": count, "probability": round(count / particles, 8)}
                for rid, count in sorted(rows.items())
            ]
            for asset, rows in sorted(controller_counts.items())
        },
        "predraft_event_audit": predraft["audit"],
        "representative_postdraft_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted((group.state.get("roster_players") or {}).get(focus, [])),
                "draft": (group.state.get(DRAFT_LEDGER_KEY) or {}).get(draft_season),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda x: x.count, reverse=True)[:20]
        ],
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/rookie_draft_particles_0_7f.json", report
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 0.7f pre-draft replay and branch-specific rookie draft")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
