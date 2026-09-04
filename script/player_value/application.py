#!/usr/bin/env python3
"""Build an independent, league-specific current-season player-value ranking.

The application converts published football projections into FSFFL scoring and
roster economics.  It does not consume dynasty market value, GM3 utility,
strategic posture, trade behavior, or recommendation outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
MODEL_VERSION = "FSFFL-Player-Value-Current-Season-1.0"
POSITIONS = ("QB", "RB", "WR", "TE")
FLEX_ELIGIBLE = ("RB", "WR", "TE")
SUPERFLEX_ELIGIBLE = POSITIONS
SLOT_ELIGIBILITY = {
    "QB": ("QB",),
    "RB": ("RB",),
    "WR": ("WR",),
    "TE": ("TE",),
    "FLEX": FLEX_ELIGIBLE,
    "SUPER_FLEX": SUPERFLEX_ELIGIBLE,
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _allocations(total: int, eligible: tuple[str, ...]) -> Iterable[dict[str, int]]:
    """Yield all integer allocations of repeated flexible slots."""
    if total == 0:
        yield {position: 0 for position in eligible}
        return
    for values in product(range(total + 1), repeat=len(eligible) - 1):
        used = sum(values)
        if used > total:
            continue
        last = total - used
        yield dict(zip(eligible, (*values, last)))


def _regular_season_weeks(league: Mapping[str, Any]) -> list[int]:
    settings = league.get("settings") or {}
    start = int(settings.get("start_week") or 1)
    playoff_start = int(settings.get("playoff_week_start") or 15)
    return list(range(start, playoff_start))


def _slot_counts(league: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw in league.get("roster_positions") or []:
        slot = str(raw).upper().replace("SUPERFLEX", "SUPER_FLEX")
        if slot in {*POSITIONS, "FLEX", "SUPER_FLEX"}:
            counts[slot] = counts.get(slot, 0) + 1
    return counts


def _lineup_slots(league: Mapping[str, Any]) -> list[str]:
    slots = []
    for raw in league.get("roster_positions") or []:
        slot = str(raw).upper().replace("SUPERFLEX", "SUPER_FLEX")
        if slot in SLOT_ELIGIBILITY:
            slots.append(slot)
    return slots


def _player_expected_points(profile: Mapping[str, Any], weeks: Iterable[int]) -> tuple[float, float, int]:
    expected = 0.0
    variance = 0.0
    covered = 0
    weekly = profile.get("weeks") or {}
    for week in weeks:
        row = weekly.get(str(week))
        if not isinstance(row, Mapping):
            continue
        covered += 1
        active = max(0.0, min(1.0, _number(row.get("active_probability", 1.0))))
        mean = _number(row.get("mean"))
        sd = max(0.0, _number(row.get("sd")))
        expected += active * mean
        # Bernoulli availability around the conditional weekly distribution.
        variance += active * sd * sd + active * (1.0 - active) * mean * mean
    return expected, variance ** 0.5, covered


def _weekly_expected(profile: Mapping[str, Any], week: int) -> float:
    row = (profile.get("weeks") or {}).get(str(week)) or {}
    active = max(0.0, min(1.0, _number(row.get("active_probability", 1.0))))
    return active * _number(row.get("mean"))


def _roster_lineup_frontier(
    roster_player_ids: Iterable[str],
    projection_players: Mapping[str, Mapping[str, Any]],
    slots: list[str],
    week: int,
) -> tuple[float, dict[str, float]]:
    """Return baseline lineup points and best pre-addition value by position.

    Dynamic programming assigns every existing player at most once. For a new
    player, the best lineup containing that player is the best existing lineup
    that leaves one eligible slot open plus the player's expected points.
    """
    dp = {0: 0.0}
    for player_id in roster_player_ids:
        profile = projection_players.get(str(player_id)) or {}
        position = str(profile.get("position") or "").upper()
        if position not in POSITIONS:
            continue
        value = _weekly_expected(profile, week)
        if value <= 0:
            continue
        updated = dict(dp)
        for mask, total in dp.items():
            for index, slot in enumerate(slots):
                bit = 1 << index
                if mask & bit or position not in SLOT_ELIGIBILITY[slot]:
                    continue
                new_mask = mask | bit
                updated[new_mask] = max(updated.get(new_mask, float("-inf")), total + value)
        dp = updated
    baseline = max(dp.values(), default=0.0)
    before_addition: dict[str, float] = {}
    for position in POSITIONS:
        candidates = []
        for index, slot in enumerate(slots):
            if position not in SLOT_ELIGIBILITY[slot]:
                continue
            bit = 1 << index
            candidates.append(max((total for mask, total in dp.items() if not mask & bit), default=0.0))
        before_addition[position] = max(candidates, default=float("-inf"))
    return baseline, before_addition


def _league_average_marginal_points(
    projection_players: Mapping[str, Mapping[str, Any]],
    rosters: list[Mapping[str, Any]],
    league: Mapping[str, Any],
    weeks: list[int],
) -> tuple[dict[str, float], dict[str, int]]:
    """Exact mean acquisition-side lineup gain across actual non-owner rosters."""
    slots = _lineup_slots(league)
    active_rosters = []
    for roster in rosters:
        roster_id = str(roster.get("roster_id") or roster.get("owner_id") or len(active_rosters) + 1)
        taxi = {str(player_id) for player_id in (roster.get("taxi") or [])}
        players = [str(player_id) for player_id in (roster.get("players") or []) if str(player_id) not in taxi]
        active_rosters.append((roster_id, players))

    totals = {str(player_id): 0.0 for player_id in projection_players}
    contexts = {str(player_id): 0 for player_id in projection_players}
    for roster_id, roster_players in active_rosters:
        roster_set = set(roster_players)
        season_gain = {str(player_id): 0.0 for player_id in projection_players if str(player_id) not in roster_set}
        for week in weeks:
            baseline, before_addition = _roster_lineup_frontier(
                roster_players, projection_players, slots, week
            )
            for player_id, profile in projection_players.items():
                player_id = str(player_id)
                if player_id in roster_set:
                    continue
                position = str(profile.get("position") or "").upper()
                if position not in POSITIONS:
                    continue
                with_player = before_addition[position] + _weekly_expected(profile, week)
                season_gain[player_id] += max(0.0, with_player - baseline)
        for player_id, gain in season_gain.items():
            totals[player_id] += gain
            contexts[player_id] += 1
    averages = {
        player_id: totals[player_id] / contexts[player_id]
        for player_id in totals if contexts[player_id] > 0
    }
    return averages, contexts


def _best_league_allocation(
    points_by_position: Mapping[str, list[float]],
    base_counts: Mapping[str, int],
    flex_count: int,
    superflex_count: int,
) -> tuple[float, dict[str, int]]:
    prefix: dict[str, list[float]] = {}
    for position in POSITIONS:
        values = sorted(points_by_position.get(position) or [], reverse=True)
        running = [0.0]
        for value in values:
            running.append(running[-1] + value)
        prefix[position] = running

    best_total = float("-inf")
    best_counts = {position: int(base_counts.get(position, 0)) for position in POSITIONS}
    for flex in _allocations(flex_count, FLEX_ELIGIBLE):
        for superflex in _allocations(superflex_count, SUPERFLEX_ELIGIBLE):
            counts = {
                position: int(base_counts.get(position, 0))
                + int(flex.get(position, 0))
                + int(superflex.get(position, 0))
                for position in POSITIONS
            }
            if any(counts[p] >= len(prefix[p]) for p in POSITIONS):
                continue
            total = sum(prefix[p][counts[p]] for p in POSITIONS)
            if total > best_total:
                best_total = total
                best_counts = counts
    if best_total == float("-inf"):
        raise ValueError("projected player universe cannot fill the rule-defined league lineup")
    return best_total, best_counts


def _league_allocation_candidates(
    points_by_position: Mapping[str, list[float]],
    base_counts: Mapping[str, int],
    flex_count: int,
    superflex_count: int,
) -> tuple[dict[str, list[float]], list[tuple[float, dict[str, int]]]]:
    """Precompute every feasible rule-derived position allocation and its value."""
    prefix: dict[str, list[float]] = {}
    for position in POSITIONS:
        values = sorted(points_by_position.get(position) or [], reverse=True)
        running = [0.0]
        for value in values:
            running.append(running[-1] + value)
        prefix[position] = running
    candidates: list[tuple[float, dict[str, int]]] = []
    for flex in _allocations(flex_count, FLEX_ELIGIBLE):
        for superflex in _allocations(superflex_count, SUPERFLEX_ELIGIBLE):
            counts = {
                position: int(base_counts.get(position, 0))
                + int(flex.get(position, 0))
                + int(superflex.get(position, 0))
                for position in POSITIONS
            }
            if any(counts[p] >= len(prefix[p]) for p in POSITIONS):
                continue
            candidates.append((sum(prefix[p][counts[p]] for p in POSITIONS), counts))
    if not candidates:
        raise ValueError("projected player universe cannot fill the rule-defined league lineup")
    return prefix, candidates


def _competition_ranks(rows: list[dict[str, Any]], key: str) -> None:
    ordered = sorted(rows, key=lambda row: (-_number(row.get(key)), str(row.get("name") or "")))
    previous: float | None = None
    rank = 0
    for index, row in enumerate(ordered, 1):
        value = _number(row.get(key))
        if previous is None or abs(value - previous) > 1e-9:
            rank = index
            previous = value
        row[f"{key}_rank"] = rank


def build_current_season_values(
    league_path: Path,
    projections_path: Path,
    rosters_path: Path,
) -> dict[str, Any]:
    league = _load(league_path)
    projections = _load(projections_path)
    rosters = _load(rosters_path)
    league_size = len(rosters)
    if league_size <= 0:
        raise ValueError("at least one league roster is required")
    weeks = _regular_season_weeks(league)
    slots = _slot_counts(league)
    base_counts = {position: slots.get(position, 0) * league_size for position in POSITIONS}
    flex_count = slots.get("FLEX", 0) * league_size
    superflex_count = slots.get("SUPER_FLEX", 0) * league_size

    rows: list[dict[str, Any]] = []
    for player_id, profile in (projections.get("players") or {}).items():
        position = str(profile.get("position") or "").upper()
        if position not in POSITIONS:
            continue
        expected, uncertainty, covered = _player_expected_points(profile, weeks)
        if covered == 0:
            continue
        rows.append({
            "player_id": str(player_id),
            "name": profile.get("name"),
            "position": position,
            "nfl_team": profile.get("team"),
            "regular_season_expected_points": round(expected, 3),
            "regular_season_uncertainty_sd": round(uncertainty, 3),
            "weeks_covered": covered,
        })

    points_by_position = {
        position: [row["regular_season_expected_points"] for row in rows if row["position"] == position]
        for position in POSITIONS
    }
    prefix, allocation_candidates = _league_allocation_candidates(
        points_by_position, base_counts, flex_count, superflex_count
    )
    baseline, selected_counts = max(allocation_candidates, key=lambda item: item[0])
    replacement_frontier = {
        position: (
            sorted(points_by_position[position], reverse=True)[selected_counts[position] - 1]
            if selected_counts[position] else 0.0
        )
        for position in POSITIONS
    }

    roster_marginal, roster_context_counts = _league_average_marginal_points(
        projections.get("players") or {}, rosters, league, weeks
    )

    # The synthetic global starter frontier remains a transparent scarcity
    # diagnostic. Ranking authority is the actual-roster mean calculated above.
    position_order = {
        position: sorted(
            [row for row in rows if row["position"] == position],
            key=lambda item: (-item["regular_season_expected_points"], str(item.get("name") or "")),
        )
        for position in POSITIONS
    }
    position_index = {
        row["player_id"]: index
        for position in POSITIONS
        for index, row in enumerate(position_order[position])
    }
    for row in rows:
        position = row["position"]
        index = position_index[row["player_id"]]
        value = row["regular_season_expected_points"]
        reduced = float("-inf")
        for candidate_total, counts in allocation_candidates:
            count = counts[position]
            if count > len(position_order[position]) - 1:
                continue
            adjusted = candidate_total
            if index < count:
                # Removing a selected player advances the next player at this
                # position into the candidate allocation.
                adjusted += prefix[position][count + 1] - value - prefix[position][count]
            reduced = max(reduced, adjusted)
        starter_frontier_marginal = max(0.0, baseline - reduced)
        marginal = roster_marginal.get(row["player_id"], 0.0)
        row["fsffl_current_season_value"] = round(marginal, 3)
        row["league_average_marginal_lineup_points"] = round(marginal, 3)
        row["actual_roster_contexts_evaluated"] = roster_context_counts.get(row["player_id"], 0)
        row["global_starter_frontier_marginal_points"] = round(starter_frontier_marginal, 3)
        row["position_replacement_frontier_points"] = round(replacement_frontier[row["position"]], 3)
        row["projected_points_vs_position_frontier"] = round(
            row["regular_season_expected_points"] - replacement_frontier[row["position"]], 3
        )
        row["expected_lineup_contributor"] = marginal > 1e-9

    _competition_ranks(rows, "fsffl_current_season_value")
    by_position: dict[str, list[dict[str, Any]]] = {
        position: [row for row in rows if row["position"] == position] for position in POSITIONS
    }
    for position_rows in by_position.values():
        _competition_ranks(position_rows, "fsffl_current_season_value")
        for row in position_rows:
            row["fsffl_current_season_position_rank"] = row.pop("fsffl_current_season_value_rank")
    # Recreate overall ranks after the position pass overwrote the temporary key.
    _competition_ranks(rows, "fsffl_current_season_value")
    for row in rows:
        row["fsffl_current_season_rank"] = row.pop("fsffl_current_season_value_rank")

    rows.sort(key=lambda row: (row["fsffl_current_season_rank"], -row["regular_season_expected_points"], str(row["name"])))
    sources = {
        "league_rules": {"path": str(league_path.relative_to(ROOT)), "sha256": _sha256(league_path)},
        "projection_system": {"path": str(projections_path.relative_to(ROOT)), "sha256": _sha256(projections_path)},
        "league_rosters": {"path": str(rosters_path.relative_to(ROOT)), "sha256": _sha256(rosters_path)},
    }
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "season": projections.get("season"),
        "status": "CURRENT_SEASON_GOVERNED",
        "purpose": "Independent FSFFL current-season contribution ranking",
        "value_semantics": (
            "Mean exact expected regular-season lineup-point gain from adding the player to each "
            "actual FSFFL roster that does not already own him, with legal QB/RB/WR/TE/FLEX/Superflex optimization"
        ),
        "not_a_trade_price": True,
        "market_inputs_consumed": False,
        "gm3_inputs_consumed": False,
        "strategic_posture_consumed": False,
        "recommendation": False,
        "league_context": {
            "league_size": league_size,
            "regular_season_weeks": weeks,
            "starter_slots_per_team": slots,
            "optimal_position_allocation": selected_counts,
            "replacement_frontier_points": {k: round(v, 3) for k, v in replacement_frontier.items()},
            "team_neutral_aggregation": "unweighted mean across every non-owner league roster",
        },
        "source_manifest": sources,
        "long_term_model": {
            "available": False,
            "status": "REQUIRES_TEMPORAL_VALIDATION",
            "reason": "No governed multi-year player projection has cleared time-ordered promotion.",
        },
        "players": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", type=Path, default=ROOT / "data" / "league.json")
    parser.add_argument(
        "--projections", type=Path,
        default=ROOT / "data" / "simulator" / "2026" / "inputs" / "player_weekly_projections.json",
    )
    parser.add_argument("--rosters", type=Path, default=ROOT / "data" / "rosters.json")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data" / "player_value" / "fsffl_current_season_value.json",
    )
    args = parser.parse_args()
    payload = build_current_season_values(args.league, args.projections, args.rosters)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "players": len(payload["players"]),
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
