#!/usr/bin/env python3
"""FSFFL Alternate History 0.7c: exact Max PF reconstruction/backvalidation.

FSFFL non-playoff rookie slots 1-6 are Max Points For ascending. Before that
rule is allowed to affect counterfactual branches, reconstruct actual historical
Max PF directly from weekly roster/player scoring artifacts and verify that the
result reproduces the observed following rookie-draft order.

Max PF here means the sum, across regular-season weeks, of the highest-scoring
legal lineup available from the rostered players in that week's Sleeper matchup
snapshot. Completed NFL/fantasy scoring is immutable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_postseason_consequences_v3 import run as run_postseason

DATA = Path("data")
REGULAR_SEASON_WEEKS = tuple(range(1, 15))
STARTER_SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX")
BASE_COUNTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
FLEX_POSITIONS = ("RB", "WR", "TE")
SUPERFLEX_POSITIONS = ("QB", "RB", "WR", "TE")


def player_positions() -> Dict[str, str]:
    players = load(DATA / "players.json")
    return {str(pid): str(row.get("position") or "").upper() for pid, row in players.items()}


def max_lineup(points: Dict[str, float], positions: Dict[str, str]) -> Dict[str, Any]:
    """Return exact optimal FSFFL lineup in O(players log players + 12 allocations).

    Every legal lineup is fully described by the position used in FLEX and the
    position used in SUPER_FLEX. There are only 3 x 4 = 12 such allocations.
    For each allocation the optimal players are simply the top N scorers at each
    required position. This is exactly equivalent to generic assignment search
    but dramatically cheaper.
    """
    by_pos: Dict[str, List[tuple[str, float]]] = {p: [] for p in ("QB", "RB", "WR", "TE")}
    for pid, value in points.items():
        pos = positions.get(str(pid), "")
        if pos in by_pos:
            by_pos[pos].append((str(pid), float(value or 0.0)))
    for pos in by_pos:
        by_pos[pos].sort(key=lambda row: (row[1], row[0]), reverse=True)

    best_score = float("-inf")
    best_counts: Dict[str, int] = {}
    best_flex = None
    best_superflex = None
    for flex_pos in FLEX_POSITIONS:
        for sf_pos in SUPERFLEX_POSITIONS:
            counts = dict(BASE_COUNTS)
            counts[flex_pos] += 1
            counts[sf_pos] += 1
            feasible = all(len(by_pos[pos]) >= needed for pos, needed in counts.items())
            if not feasible:
                continue
            score = sum(
                sum(value for _, value in by_pos[pos][:needed])
                for pos, needed in counts.items()
            )
            if score > best_score:
                best_score = score
                best_counts = counts
                best_flex = flex_pos
                best_superflex = sf_pos

    # Defensive fallback for an incomplete historical roster snapshot. We never
    # invent a player; missing starter slots are zero. This should not occur in
    # normal FSFFL weekly snapshots but keeps the audit explicit.
    if best_score == float("-inf"):
        counts = dict(BASE_COUNTS)
        best_score = 0.0
        for pos, needed in counts.items():
            best_score += sum(value for _, value in by_pos[pos][:needed])
        best_counts = counts

    selected_by_pos = {
        pos: list(by_pos[pos][:needed]) for pos, needed in best_counts.items()
    }
    # Reconstruct a readable slot audit. Fixed slots consume the first players;
    # FLEX/SF consume any extra selected player of their designated position.
    cursors = {p: 0 for p in selected_by_pos}
    lineup = []
    for slot in ("QB", "RB", "RB", "WR", "WR", "WR", "TE"):
        rows = selected_by_pos.get(slot, [])
        idx = cursors.get(slot, 0)
        if idx < len(rows):
            pid, pts = rows[idx]
            cursors[slot] = idx + 1
            lineup.append({"slot": slot, "player_id": pid, "position": slot, "points": round(pts, 2)})
        else:
            lineup.append({"slot": slot, "player_id": None, "position": None, "points": 0.0})
    for slot, pos in (("FLEX", best_flex), ("SUPER_FLEX", best_superflex)):
        if pos is None:
            lineup.append({"slot": slot, "player_id": None, "position": None, "points": 0.0})
            continue
        rows = selected_by_pos.get(pos, [])
        idx = cursors.get(pos, 0)
        if idx < len(rows):
            pid, pts = rows[idx]
            cursors[pos] = idx + 1
            lineup.append({"slot": slot, "player_id": pid, "position": pos, "points": round(pts, 2)})
        else:
            lineup.append({"slot": slot, "player_id": None, "position": None, "points": 0.0})

    return {
        "max_points": round(float(best_score), 2),
        "lineup": lineup,
        "flex_position": best_flex,
        "superflex_position": best_superflex,
    }


def actual_weekly_maxpf(season: str, positions: Dict[str, str]) -> Dict[str, Any]:
    raw = load(DATA / "stats" / "fsffl" / str(season) / "league_matchups_raw.json")
    totals: Dict[str, float] = {}
    weekly: Dict[str, Dict[str, Any]] = {}
    missing_weeks: List[int] = []
    for week in REGULAR_SEASON_WEEKS:
        rows = raw.get(str(week)) or raw.get(week) or []
        if not rows:
            missing_weeks.append(week)
            continue
        for row in rows:
            rid = str(row.get("roster_id"))
            points = {str(pid): float(value or 0.0) for pid, value in (row.get("players_points") or {}).items()}
            result = max_lineup(points, positions)
            totals[rid] = totals.get(rid, 0.0) + float(result["max_points"])
            weekly.setdefault(rid, {})[str(week)] = result
    return {
        "totals": {rid: round(value, 2) for rid, value in totals.items()},
        "weekly": weekly,
        "missing_regular_season_weeks": missing_weeks,
    }


def run(scenario_path: Path) -> Path:
    post = load(run_postseason(scenario_path))
    season = str(post.get("season"))
    positions = player_positions()
    maxpf = actual_weekly_maxpf(season, positions)

    observed = {str(k): int(v) for k, v in (post.get("actual", {}).get("following_draft_order_observed") or {}).items()}
    playoff = {str(k) for k in (post.get("actual", {}).get("playoffs", {}).get("finish_by_roster") or {}).keys()}
    nonplay_ids = sorted((rid for rid in observed if rid not in playoff), key=lambda rid: observed[rid])

    standings = post.get("actual", {}).get("regular_season", {}).get("standings") or {}
    def losses(rid: str) -> int:
        row = standings.get(str(rid)) or {}
        return int(row.get("losses") or 0)

    reconstructed = sorted(
        nonplay_ids,
        key=lambda rid: (float(maxpf["totals"].get(rid, float("inf"))), -losses(rid), rid),
    )
    checks = []
    valid = len(nonplay_ids) == 6 and not maxpf["missing_regular_season_weeks"]
    for slot, rid in enumerate(reconstructed, 1):
        observed_slot = observed.get(rid)
        ok = observed_slot == slot
        checks.append({
            "roster_id": rid,
            "max_pf": maxpf["totals"].get(rid),
            "regular_season_losses": losses(rid),
            "reconstructed_slot": slot,
            "observed_slot": observed_slot,
            "match": ok,
        })
        valid = valid and ok

    report = {
        "model_version": "Fantasy-Alternate-History-0.7c-maxpf-draft-order",
        "scenario_id": post.get("scenario_id"),
        "season": season,
        "following_draft_season": str(int(season) + 1),
        "design_invariants": {
            "completed_nfl_fantasy_points_are_immutable": True,
            "weekly_actual_roster_snapshots_used": True,
            "exact_lineup_optimization_used": True,
            "finite_position_allocation_solver_used": True,
            "current_gm3_values_used": False,
        },
        "lineup_slots": list(STARTER_SLOTS),
        "regular_season_weeks": list(REGULAR_SEASON_WEEKS),
        "actual_max_pf_by_roster": maxpf["totals"],
        "nonplayoff_backvalidation": {
            "rule": "Max PF ascending; lower Max PF drafts earlier; exact Max PF tie falls to worse regular-season record",
            "validated": bool(valid),
            "checks": checks,
            "observed_nonplayoff_rosters_in_slot_order": nonplay_ids,
            "reconstructed_nonplayoff_rosters_in_slot_order": reconstructed,
            "missing_regular_season_weeks": maxpf["missing_regular_season_weeks"],
        },
        "weekly_maxpf_audit": maxpf["weekly"],
    }
    out = ah.write_isolated_json(
        f"results/{post.get('scenario_id')}/maxpf_draft_order_0_7c.json", report
    )
    print(out)
    print(json.dumps({
        "validated": valid,
        "observed": nonplay_ids,
        "reconstructed": reconstructed,
        "checks": checks,
    }, indent=2, sort_keys=True))
    if not valid:
        raise ah.AlternateHistoryError("0.7c actual Max PF reconstruction did not reproduce observed nonplayoff draft order")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Backvalidate FSFFL Max PF nonplayoff draft order")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
