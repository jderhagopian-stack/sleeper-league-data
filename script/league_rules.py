#!/usr/bin/env python3
"""Canonical non-projection league-rule resolver.

Rule-defined league structure comes from data/league.json (Sleeper sync), not
from FSFFL constants embedded in model helpers.  This module intentionally does
not contain projection formulas, projection weights, or projection uncertainty.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

BENCH_SLOTS = {"BN", "BENCH"}
NON_ACTIVE_SLOTS = {"IR", "RESERVE", "TAXI"}
POSITION_ALIASES = {"DST": "DEF", "D/ST": "DEF", "SUPERFLEX": "SUPER_FLEX"}

# Sleeper-standard eligibility. These are roster-rule semantics, not model weights.
FLEX_ELIGIBILITY = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "REC_FLEX": ("WR", "TE"),
    "WRTE_FLEX": ("WR", "TE"),
}

FIXED_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def normalize_slot(slot: Any) -> str:
    s = str(slot or "").upper().strip()
    return POSITION_ALIASES.get(s, s)


def normalize_position(position: Any) -> str:
    p = str(position or "").upper().strip()
    return POSITION_ALIASES.get(p, p)


def slot_eligible_positions(slot: str) -> Tuple[str, ...]:
    slot = normalize_slot(slot)
    if slot in FIXED_POSITIONS:
        return (slot,)
    return FLEX_ELIGIBILITY.get(slot, ())


def _starter_slots(roster_positions: Iterable[Any]) -> List[str]:
    out = []
    for raw in roster_positions or []:
        slot = normalize_slot(raw)
        if slot in BENCH_SLOTS or slot in NON_ACTIVE_SLOTS:
            continue
        if slot in FIXED_POSITIONS or slot in FLEX_ELIGIBILITY:
            out.append(slot)
    return out


def _active_roster_size(roster_positions: Iterable[Any]) -> int:
    # Sleeper roster_positions represents starters + bench; reserve/taxi are
    # separate settings and therefore do not consume active-roster slots.
    return sum(1 for x in (roster_positions or []) if normalize_slot(x) not in NON_ACTIVE_SLOTS)


def _future_pick_years(league: Dict[str, Any], traded_picks_path: Path | None, default_horizon: int = 3) -> List[int]:
    season = int(str(league.get("season") or "0")[:4] or 0)
    observed = set()
    if traded_picks_path:
        for row in _load(traded_picks_path, []) or []:
            try:
                y = int(row.get("season"))
            except Exception:
                continue
            if y > season:
                observed.add(y)
    # Sleeper often exposes only picks that have actually moved. Preserve the
    # existing three-year planning horizon when no farther observed year exists,
    # but make that horizon explicit and centralized rather than hidden.
    baseline = {season + i for i in range(1, default_horizon + 1)} if season else set()
    return sorted(observed | baseline)


def load_league_rules(
    league_path: Path = Path("data/league.json"),
    traded_picks_path: Path | None = Path("data/traded_picks.json"),
) -> Dict[str, Any]:
    league = _load(league_path, {}) or {}
    settings = league.get("settings") or {}
    scoring = league.get("scoring_settings") or {}
    roster_positions = league.get("roster_positions") or []

    lineup_slots = _starter_slots(roster_positions)
    positions = []
    for slot in lineup_slots:
        for pos in slot_eligible_positions(slot):
            if pos not in positions:
                positions.append(pos)

    team_count = int(settings.get("num_teams") or league.get("total_rosters") or 0)
    draft_rounds = int(settings.get("draft_rounds") or 0)
    ppr = float(scoring.get("rec") or 0.0)
    superflex = "SUPER_FLEX" in lineup_slots
    fixed_qb_starts = sum(1 for x in lineup_slots if x == "QB")
    market_num_qbs = 2 if superflex or fixed_qb_starts >= 2 else 1

    return {
        "source": str(league_path),
        "league_id": str(league.get("league_id") or ""),
        "season": int(str(league.get("season") or "0")[:4] or 0),
        "team_count": team_count,
        "roster_size": _active_roster_size(roster_positions),
        "lineup_slots": lineup_slots,
        "positions": positions or list(FIXED_POSITIONS[:4]),
        "draft_rounds": draft_rounds,
        "rounds": list(range(1, draft_rounds + 1)) if draft_rounds > 0 else [1, 2, 3],
        "future_pick_years": _future_pick_years(league, traded_picks_path),
        "ppr": ppr,
        "superflex": superflex,
        "market_num_qbs": market_num_qbs,
        "playoff_teams": int(settings.get("playoff_teams") or 0),
        "playoff_week_start": int(settings.get("playoff_week_start") or 0),
        "playoff_type": int(settings.get("playoff_type") or 0),
        "playoff_seed_type": int(settings.get("playoff_seed_type") or 0),
        "playoff_round_type": int(settings.get("playoff_round_type") or 0),
        "divisions": int(settings.get("divisions") or 0),
        "reserve_slots": int(settings.get("reserve_slots") or 0),
        "taxi_slots": int(settings.get("taxi_slots") or 0),
        "trade_deadline": int(settings.get("trade_deadline") or 0),
        "scoring_settings": scoring,
        "rule_provenance": {
            "team_count": "league.settings.num_teams",
            "roster_size": "count(league.roster_positions), reserve/taxi excluded by Sleeper schema",
            "lineup_slots": "league.roster_positions filtered to starter slots",
            "draft_rounds": "league.settings.draft_rounds",
            "ppr": "league.scoring_settings.rec",
            "superflex": "presence of SUPER_FLEX in starter slots",
            "playoffs": "league.settings.playoff_* and roster.settings.division",
        },
        "provisional_runtime_defaults": {
            "future_pick_horizon_years": 3,
            "note": "Planning horizon is a model-runtime scope, not a league rule; observed traded-pick years extend it automatically.",
        },
    }


if __name__ == "__main__":
    print(json.dumps(load_league_rules(), indent=2))
