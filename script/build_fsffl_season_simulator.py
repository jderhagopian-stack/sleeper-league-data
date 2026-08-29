#!/usr/bin/env python3
"""
FSFFL Season Simulator 1.0
Season-agnostic annual forecasting engine for FSFFL Dynasty.

Design goals
------------
- Never hard-code a league season.
- Read the current Sleeper league season/configuration from data/league.json.
- Use the actual FSFFL regular-season schedule.
- Consume player-level weekly fantasy-point distributions in exact FSFFL scoring.
- Optimize legal lineups each week.
- Monte Carlo the regular season + playoffs.
- Preserve each season under data/simulator/<season>/.
- Fail validation rather than silently substitute market value for fantasy-point projections.

Projection input
----------------
Expected path:
data/simulator/<season>/inputs/player_weekly_projections.json

Shape:
{
  "season": "2026",
  "source": "projection blend description",
  "generated_at_utc": "...",
  "players": {
    "<sleeper_player_id>": {
      "name": "...",
      "position": "QB",
      "weeks": {
        "1": {
          "mean": 20.5,
          "sd": 6.1,
          "median": 20.0,
          "p25": 16.0,
          "p75": 24.5,
          "active_probability": 0.98
        }
      }
    }
  }
}
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from league_rules import normalize_position, normalize_slot, slot_eligible_positions

DATA = Path("data")
SIM_ROOT = DATA / "simulator"
DEFAULT_SIMS = 50000
MODEL_VERSION = "FSFFL-Season-Simulator-1.0"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def user_name_map(users):
    out = {}
    for u in users or []:
        uid = str(u.get("user_id"))
        md = u.get("metadata") or {}
        out[uid] = {
            "manager": u.get("display_name") or u.get("username") or uid,
            "team_name": md.get("team_name") or u.get("display_name") or u.get("username") or uid,
        }
    return out


def roster_directory(rosters, users):
    names = user_name_map(users)
    out = {}
    for r in rosters or []:
        rid = int(r.get("roster_id"))
        uid = str(r.get("owner_id"))
        info = names.get(uid, {})
        out[rid] = {
            "roster_id": rid,
            "user_id": uid,
            "manager": info.get("manager", uid),
            "team_name": info.get("team_name", info.get("manager", uid)),
            "players": [str(x) for x in (r.get("players") or [])],
            "reserve": [str(x) for x in (r.get("reserve") or [])],
            "taxi": [str(x) for x in (r.get("taxi") or [])],
            "division": (r.get("settings") or {}).get("division"),
        }
    return out


def regular_season_weeks(league):
    settings = league.get("settings") or {}
    playoff_start = int(settings.get("playoff_week_start") or 15)
    start_week = int(settings.get("start_week") or 1)
    return list(range(start_week, playoff_start))


def lineup_slots(league):
    return [
        normalize_slot(x) for x in (league.get("roster_positions") or [])
        if normalize_slot(x) not in {"BN", "BENCH", "IR", "RESERVE", "TAXI"}
        and slot_eligible_positions(normalize_slot(x))
    ]


def eligible(position: str, slot: str):
    return normalize_position(position) in slot_eligible_positions(slot)


def build_schedule(raw_schedule, weeks):
    """
    Returns:
      by_week[week] = [(roster_a, roster_b), ...]
      opponents[roster_id][week] = opponent_roster_id
    """
    by_week = {}
    opponents = defaultdict(dict)
    for week in weeks:
        rows = (raw_schedule or {}).get(str(week)) or []
        groups = defaultdict(list)
        for row in rows:
            mid = row.get("matchup_id")
            rid = row.get("roster_id")
            if mid is None or rid is None:
                continue
            groups[str(mid)].append(int(rid))
        pairs = []
        for _, ids in sorted(groups.items()):
            if len(ids) == 2:
                a, b = ids
                pairs.append((a, b))
                opponents[a][week] = b
                opponents[b][week] = a
        by_week[week] = pairs
    return by_week, opponents


def projection_for(projections, pid, week):
    p = ((projections or {}).get("players") or {}).get(str(pid)) or {}
    row = (p.get("weeks") or {}).get(str(week))
    if not row:
        return None
    mean = float(row.get("mean", row.get("median", 0.0)) or 0.0)
    median = float(row.get("median", mean) or mean)
    sd = row.get("sd")
    if sd is None:
        p25 = row.get("p25")
        p75 = row.get("p75")
        if p25 is not None and p75 is not None:
            # For a normal-ish distribution, IQR ~= 1.349 sigma.
            sd = max(0.1, (float(p75) - float(p25)) / 1.349)
        else:
            # Only a last-resort distribution-width fallback; validation records it.
            sd = max(2.0, abs(mean) * 0.32)
    active_p = max(0.0, min(1.0, float(row.get("active_probability", 1.0) or 0.0)))
    return {
        "mean": mean,
        "median": median,
        "sd": max(0.1, float(sd)),
        "active_probability": active_p,
    }


def player_meta(players, projections, pid):
    p = (players or {}).get(str(pid)) or {}
    q = ((projections or {}).get("players") or {}).get(str(pid)) or {}
    return {
        "player_id": str(pid),
        "name": q.get("name") or p.get("full_name") or str(pid),
        "position": q.get("position") or p.get("position"),
    }


def optimize_weekly_lineup(roster, week, league, players, projections):
    """
    Optimize by weekly projected mean among players who have a projection.
    Actual fantasy managers cannot see realized future points, so lineups are
    selected from projections, not simulation outcomes.
    """
    candidates = []
    taxi = set(roster.get("taxi") or [])
    for pid in roster.get("players") or []:
        if pid in taxi:
            continue
        meta = player_meta(players, projections, pid)
        pos = meta.get("position")
        pr = projection_for(projections, pid, week)
        if not pos or pr is None or pr["active_probability"] <= 0:
            continue
        candidates.append({**meta, **pr})

    slots = lineup_slots(league)

    # Small lineups allow exact DFS optimization.
    best_value = -1e18
    best_assign = []

    # Order hardest slots first to prune better.
    slot_priority = {"QB": 0, "TE": 1, "RB": 2, "WR": 2, "SUPER_FLEX": 3, "FLEX": 4}
    ordered_slots = sorted(enumerate(slots), key=lambda x: slot_priority.get(x[1], 5))

    def dfs(i, used, total, assigned):
        nonlocal best_value, best_assign
        if i == len(ordered_slots):
            if total > best_value:
                best_value = total
                best_assign = list(assigned)
            return
        original_idx, slot = ordered_slots[i]
        opts = [c for c in candidates if c["player_id"] not in used and eligible(c["position"], slot)]
        if not opts:
            # Allow an empty slot with zero points if roster cannot legally fill it.
            dfs(i + 1, used, total, assigned + [(original_idx, slot, None)])
            return
        opts.sort(key=lambda x: x["mean"] * x["active_probability"], reverse=True)
        for c in opts:
            pid = c["player_id"]
            dfs(
                i + 1,
                used | {pid},
                total + c["mean"] * c["active_probability"],
                assigned + [(original_idx, slot, c)],
            )

    dfs(0, set(), 0.0, [])
    best_assign.sort(key=lambda x: x[0])
    lineup = []
    for _, slot, c in best_assign:
        if c is None:
            lineup.append({"slot": slot, "player_id": None, "name": "EMPTY", "position": None, "mean": 0.0, "sd": 0.1, "active_probability": 0.0})
        else:
            lineup.append({"slot": slot, **c})
    return lineup


def sample_player(row, rng):
    if row["player_id"] is None:
        return 0.0
    if rng.random() > row["active_probability"]:
        return 0.0
    # Truncated-at-zero Gaussian. It is intentionally simple in 1.0;
    # future versions can ingest empirical/quantile distributions directly.
    return max(0.0, rng.gauss(row["mean"], row["sd"]))


def team_week_score(lineup, rng):
    return sum(sample_player(row, rng) for row in lineup)


def standings_key(rid, records, points_for, points_against=None):
    """Sleeper-standard standing order: record, PF, then higher PA."""
    pa = points_against or {}
    return (records[rid], points_for[rid], pa.get(rid, 0.0), -rid)


def seed_teams(records, points_for, points_against=None, divisions=None, playoff_teams=6):
    """Seed using Sleeper's division-winner rule when divisions are configured.

    Division winners occupy the top seeds, ordered by the same standings
    tiebreakers. Remaining teams follow as wild cards. Without divisions, this
    reduces to overall standings order.
    """
    all_order = sorted(
        records,
        key=lambda rid: standings_key(rid, records, points_for, points_against),
        reverse=True,
    )
    divisions = divisions or {}
    groups = defaultdict(list)
    for rid in all_order:
        div = divisions.get(rid)
        if div is not None:
            groups[div].append(rid)

    if len(groups) <= 1:
        return all_order

    division_winners = [
        max(members, key=lambda rid: standings_key(rid, records, points_for, points_against))
        for members in groups.values() if members
    ]
    division_winners.sort(
        key=lambda rid: standings_key(rid, records, points_for, points_against),
        reverse=True,
    )
    others = [rid for rid in all_order if rid not in set(division_winners)]
    return division_winners + others


def playoff_round_count(playoff_teams: int) -> int:
    if playoff_teams == 4:
        return 2
    if playoff_teams in (6, 8):
        return 3
    raise ValueError(f"Unsupported Sleeper playoff-team count: {playoff_teams}; expected 4, 6, or 8")


def first_round_byes(playoff_teams: int) -> int:
    return 2 if playoff_teams == 6 else 0


def simulate_playoffs(seed_order, lineups, playoff_weeks, rng):
    """Simulate standard Sleeper 4-, 6-, or 8-team winner brackets.

    Ties advance the higher seed. Later rounds are reseeded highest-vs-lowest,
    matching the production behavior FSFFL previously used for its six-team
    bracket. Alternate/two-week championship structures remain separately
    governed rather than silently guessed.
    """
    n = len(seed_order)
    if n not in (4, 6, 8):
        return None
    seeds = {rid: i + 1 for i, rid in enumerate(seed_order)}

    def score(rid, week):
        return team_week_score(lineups[rid].get(week, []), rng)

    def winner(a, b, week):
        sa, sb = score(a, week), score(b, week)
        if sa == sb:
            return a if seeds[a] < seeds[b] else b
        return a if sa > sb else b

    if not playoff_weeks:
        return seed_order[0]

    alive = list(seed_order)
    week_idx = 0

    if n == 6:
        w = playoff_weeks[week_idx]
        alive = [
            seed_order[0],
            seed_order[1],
            winner(seed_order[2], seed_order[5], w),
            winner(seed_order[3], seed_order[4], w),
        ]
        week_idx += 1
    elif n == 8:
        w = playoff_weeks[week_idx]
        alive = [
            winner(seed_order[0], seed_order[7], w),
            winner(seed_order[3], seed_order[4], w),
            winner(seed_order[1], seed_order[6], w),
            winner(seed_order[2], seed_order[5], w),
        ]
        week_idx += 1

    # Four remaining teams: highest seed faces lowest, middle two play.
    if len(alive) == 4:
        if week_idx >= len(playoff_weeks):
            return min(alive, key=lambda rid: seeds[rid])
        alive = sorted(alive, key=lambda rid: seeds[rid])
        w = playoff_weeks[week_idx]
        alive = [
            winner(alive[0], alive[-1], w),
            winner(alive[1], alive[-2], w),
        ]
        week_idx += 1

    if len(alive) == 2:
        if week_idx >= len(playoff_weeks):
            return min(alive, key=lambda rid: seeds[rid])
        return winner(alive[0], alive[1], playoff_weeks[week_idx])
    return alive[0] if alive else None

def run_simulation(league, rosters, users, players, raw_schedule, projections, n_sims=DEFAULT_SIMS, seed=20260821):
    season = str(league["season"])
    roster_dir = roster_directory(rosters, users)
    reg_weeks = regular_season_weeks(league)
    by_week, opponents = build_schedule(raw_schedule, reg_weeks)

    settings = league.get("settings") or {}
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or 6)
    playoff_weeks = [
        playoff_start + i for i in range(playoff_round_count(playoff_teams))
    ]

    # Pre-optimize projected lineups for every regular-season and playoff week.
    all_needed_weeks = sorted(set(reg_weeks + playoff_weeks))
    lineups = defaultdict(dict)
    for rid, roster in roster_dir.items():
        for week in all_needed_weeks:
            lineups[rid][week] = optimize_weekly_lineup(roster, week, league, players, projections)

    rng = random.Random(seed)
    counts = defaultdict(lambda: defaultdict(int))
    win_totals = defaultdict(float)
    pf_totals = defaultdict(float)
    seed_counts = defaultdict(lambda: defaultdict(int))
    division_win_counts = defaultdict(int)
    title_counts = defaultdict(int)

    bye_count = first_round_byes(playoff_teams)

    # Division members.
    division_members = defaultdict(list)
    for rid, info in roster_dir.items():
        division_members[info.get("division")].append(rid)

    for _ in range(n_sims):
        wins = defaultdict(int)
        pf = defaultdict(float)
        pa = defaultdict(float)

        for week in reg_weeks:
            weekly_scores = {}
            for rid in roster_dir:
                weekly_scores[rid] = team_week_score(lineups[rid][week], rng)
                pf[rid] += weekly_scores[rid]

            for a, b in by_week.get(week, []):
                sa, sb = weekly_scores[a], weekly_scores[b]
                pa[a] += sb
                pa[b] += sa
                if sa >= sb:
                    wins[a] += 1
                else:
                    wins[b] += 1

        divisions_by_rid = {rid: info.get("division") for rid, info in roster_dir.items()}
        order = seed_teams(
            wins, pf, pa, divisions=divisions_by_rid, playoff_teams=playoff_teams
        )

        for div, members in division_members.items():
            if div is None or not members:
                continue
            winner = max(members, key=lambda rid: (wins[rid], pf[rid], -rid))
            division_win_counts[winner] += 1

        for i, rid in enumerate(order, start=1):
            seed_counts[rid][i] += 1
            win_totals[rid] += wins[rid]
            pf_totals[rid] += pf[rid]
            if i <= playoff_teams:
                counts[rid]["playoff"] += 1
            if i <= bye_count:
                counts[rid]["bye"] += 1

        # Playoffs require projection coverage for the configured bracket weeks.
        playoff_projection_complete = all(
            lineups[rid].get(w) and any(x["player_id"] is not None for x in lineups[rid][w])
            for rid in order[:playoff_teams]
            for w in playoff_weeks
        )
        if playoff_projection_complete:
            champ = simulate_playoffs(order[:playoff_teams], lineups, playoff_weeks, rng)
            if champ is not None:
                title_counts[champ] += 1

    teams = []
    for rid, info in roster_dir.items():
        teams.append({
            "roster_id": rid,
            "user_id": info["user_id"],
            "manager": info["manager"],
            "team_name": info["team_name"],
            "division": info.get("division"),
            "expected_wins": round(win_totals[rid] / n_sims, 3),
            "expected_losses": round(len(reg_weeks) - (win_totals[rid] / n_sims), 3),
            "expected_points_for": round(pf_totals[rid] / n_sims, 2),
            "playoff_probability": round(counts[rid]["playoff"] / n_sims, 5),
            "bye_probability": round(counts[rid]["bye"] / n_sims, 5),
            "division_probability": round(division_win_counts[rid] / n_sims, 5) if info.get("division") is not None else None,
            "championship_probability": round(title_counts[rid] / n_sims, 5),
            "seed_probabilities": {
                str(seed): round(seed_counts[rid][seed] / n_sims, 5)
                for seed in range(1, len(roster_dir) + 1)
            },
        })
    teams.sort(key=lambda x: (-x["expected_wins"], -x["expected_points_for"]))

    return {
        "generated_at_utc": now_utc(),
        "model_version": MODEL_VERSION,
        "season": season,
        "simulations": n_sims,
        "regular_season_weeks": reg_weeks,
        "playoff_weeks": playoff_weeks,
        "teams": teams,
        "schedule": {
            str(w): [{"a": a, "b": b} for a, b in by_week.get(w, [])]
            for w in reg_weeks
        },
        "lineups": {
            str(rid): {
                str(w): [
                    {
                        "slot": row["slot"],
                        "player_id": row["player_id"],
                        "name": row["name"],
                        "position": row["position"],
                        "mean": round(row["mean"], 3),
                        "sd": round(row["sd"], 3),
                        "active_probability": round(row["active_probability"], 4),
                    }
                    for row in lineups[rid][w]
                ]
                for w in all_needed_weeks
            }
            for rid in roster_dir
        },
    }


def validate_inputs(league, rosters, users, players, raw_schedule, projections):
    season = str(league.get("season"))
    reg_weeks = regular_season_weeks(league)
    roster_dir = roster_directory(rosters, users)
    by_week, _ = build_schedule(raw_schedule, reg_weeks)

    checks = []

    def check(code, passed, message, severity="error"):
        checks.append({"code": code, "passed": bool(passed), "severity": severity, "message": message})

    check("LEAGUE_SEASON", bool(season), f"Detected season: {season}")
    check("ROSTER_COUNT_MATCHES_LEAGUE", len(roster_dir) == int((league.get("settings") or {}).get("num_teams") or league.get("total_rosters") or len(roster_dir)),
          f"Roster count: {len(roster_dir)}")
    schedule_ok = all(len(by_week.get(w, [])) == len(roster_dir)//2 for w in reg_weeks)
    check("FULL_REGULAR_SCHEDULE", schedule_ok,
          f"Expected {len(reg_weeks)} weeks with {len(roster_dir)//2} matchups each.")
    projection_exists = isinstance(projections, dict) and bool((projections or {}).get("players"))
    check("PROJECTION_FEED", projection_exists,
          "Player weekly projection feed is present." if projection_exists else
          "Missing player weekly projection feed. Simulator will not use market value as a silent substitute.")

    rostered = {pid for info in roster_dir.values() for pid in info.get("players", [])}
    covered = set()
    week_rows = 0
    fallback_sd_rows = 0
    playoff_covered = set()
    if projection_exists:
        for pid in rostered:
            p = (projections.get("players") or {}).get(pid) or {}
            weeks = p.get("weeks") or {}
            if any(str(w) in weeks for w in reg_weeks):
                covered.add(pid)
            if all(str(w) in weeks for w in playoff_weeks):
                playoff_covered.add(pid)
            for w in reg_weeks + playoff_weeks:
                row = weeks.get(str(w))
                if row:
                    week_rows += 1
                    if row.get("sd") is None and not (row.get("p25") is not None and row.get("p75") is not None):
                        fallback_sd_rows += 1

    coverage = len(covered) / max(1, len(rostered))
    check("ROSTER_PROJECTION_COVERAGE", coverage >= 0.95,
          f"Regular-season projection coverage: {coverage:.1%} of rostered players.")
    check("PLAYOFF_PROJECTION_COVERAGE", len(playoff_covered) / max(1, len(rostered)) >= 0.95,
          f"Configured playoff weeks {playoff_weeks} projection coverage: {len(playoff_covered) / max(1, len(rostered)):.1%}.",
          severity="warning")
    check("DISTRIBUTION_WIDTHS", fallback_sd_rows == 0,
          f"{fallback_sd_rows} projection rows require generic SD fallback.",
          severity="warning")

    hard_fail = any((not c["passed"]) and c["severity"] == "error" for c in checks)
    return {
        "generated_at_utc": now_utc(),
        "model_version": MODEL_VERSION,
        "season": season,
        "validation_passed": not hard_fail,
        "checks": checks,
    }


def main():
    league = load_json(DATA / "league.json")
    rosters = load_json(DATA / "rosters.json", [])
    users = load_json(DATA / "users.json", [])
    players = load_json(DATA / "players.json", {})

    if not league:
        raise RuntimeError("data/league.json is required.")

    season = str(league.get("season"))
    season_dir = SIM_ROOT / season
    inputs_dir = season_dir / "inputs"
    outputs_dir = season_dir / "outputs"

    schedule_path = DATA / "stats" / "fsffl" / season / "league_matchups_raw.json"
    raw_schedule = load_json(schedule_path, {})
    projections_path = inputs_dir / "player_weekly_projections.json"
    projections = load_json(projections_path)

    manifest = {
        "generated_at_utc": now_utc(),
        "model_version": MODEL_VERSION,
        "season": season,
        "league_id": league.get("league_id"),
        "league_name": league.get("name"),
        "season_directory": str(season_dir),
        "regular_season_weeks": regular_season_weeks(league),
        "playoff_week_start": (league.get("settings") or {}).get("playoff_week_start"),
        "playoff_teams": (league.get("settings") or {}).get("playoff_teams"),
        "lineup_slots": lineup_slots(league),
        "projection_input": str(projections_path),
        "schedule_input": str(schedule_path),
    }
    write_json(season_dir / "season_manifest.json", manifest)

    validation = validate_inputs(league, rosters, users, players, raw_schedule, projections)
    write_json(outputs_dir / "validation_report.json", validation)

    if not validation["validation_passed"]:
        print(json.dumps(validation, indent=2))
        raise SystemExit(
            "Simulator inputs are not yet production-ready. "
            "See validation_report.json. This is intentional: "
            "1.0 will not masquerade market value as fantasy-point projection."
        )

    result = run_simulation(
        league=league,
        rosters=rosters,
        users=users,
        players=players,
        raw_schedule=raw_schedule,
        projections=projections,
        n_sims=DEFAULT_SIMS,
    )

    write_json(outputs_dir / "season_simulation.json", result)
    write_json(outputs_dir / "standings_projection.json", {
        "generated_at_utc": result["generated_at_utc"],
        "model_version": MODEL_VERSION,
        "season": season,
        "teams": result["teams"],
    })
    write_json(outputs_dir / "weekly_optimized_lineups.json", {
        "generated_at_utc": result["generated_at_utc"],
        "model_version": MODEL_VERSION,
        "season": season,
        "lineups": result["lineups"],
    })
    write_json(outputs_dir / "schedule_matrix.json", {
        "generated_at_utc": result["generated_at_utc"],
        "model_version": MODEL_VERSION,
        "season": season,
        "schedule": result["schedule"],
    })

    print(
        f"FSFFL Season Simulator 1.0 complete for {season}: "
        f"{DEFAULT_SIMS:,} simulations."
    )


if __name__ == "__main__":
    main()
