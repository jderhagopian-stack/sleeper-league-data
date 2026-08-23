#!/usr/bin/env python3
"""Alternate History 0.4 v3: postseason + raw historical draft-order validation.

Identical playoff logic to v2, but following-year draft order prefers the
isolated raw Sleeper linked-season cache. This can validate draft consequences
without relying on the current-season-only canonical drafts.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

import alternate_history_engine as ah
from run_fsffl_counterfactual_replay import run as run_direct
from run_fsffl_postseason_consequences import (
    championship_bracket,
    expected_draft_order,
    inferred_pairs,
    load,
    observed_playoff_pairs,
    owner_maps,
    score_override_index,
    standings,
    weekly_rows,
)

DATA = Path("data")


def following_draft_order_raw(season: str, user_to_roster: Dict[str, str]) -> Optional[Dict[str, int]]:
    target = str(int(season) + 1)
    cache = load(DATA / "alternate_history" / "source_history" / "sleeper_history.json")
    for season_data in cache.get("history") or []:
        league = season_data.get("league") or {}
        if str(league.get("season")) != target:
            continue
        for entry in season_data.get("drafts") or []:
            draft = entry.get("draft") or {}
            if str(draft.get("season")) != target:
                continue
            raw = draft.get("draft_order") or {}
            out: Dict[str, int] = {}
            for uid, slot in raw.items():
                rid = user_to_roster.get(str(uid))
                if rid is not None:
                    out[str(rid)] = int(slot)
            if out:
                return out
    # Fallback to canonical current drafts only if the raw historical cache
    # genuinely lacks the target season.
    for entry in load(DATA / "drafts.json"):
        draft = entry.get("draft") or {}
        if str(draft.get("season")) != target:
            continue
        out = {}
        for uid, slot in (draft.get("draft_order") or {}).items():
            rid = user_to_roster.get(str(uid))
            if rid is not None:
                out[str(rid)] = int(slot)
        if out:
            return out
    return None


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    season = str(payload.get("fork_season") or "")
    if not season:
        raise ah.AlternateHistoryError("Scenario requires fork_season")

    direct = load(run_direct(scenario_path))
    matchups = load(DATA / "stats" / "fsffl" / season / "league_matchups_raw.json")
    league = load(DATA / "league.json")
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    playoff_teams = int((league.get("settings") or {}).get("playoff_teams") or 6)
    if playoff_teams != 6:
        raise ah.AlternateHistoryError(f"0.4 currently validates six-team brackets; league has {playoff_teams}")

    rows = weekly_rows(matchups)
    overrides = score_override_index(direct)
    actual_seeded = standings(direct.get("league_regular_season_actual") or {})
    alternate_seeded = standings(direct.get("league_regular_season_alternate") or {})

    actual_bracket = championship_bracket(actual_seeded, rows, {}, playoff_start)
    actual_field = {str(x["roster_id"]) for x in actual_bracket["playoff_field"]}
    observed = observed_playoff_pairs(matchups, playoff_start, actual_field)
    inferred = inferred_pairs(actual_bracket)
    checks = {
        "week15_exact": observed.get(playoff_start, []) == inferred.get(playoff_start, []),
        "week16_contains_semifinals": set(inferred.get(playoff_start + 1, [])).issubset(set(observed.get(playoff_start + 1, []))),
        "week17_contains_title_and_third": set(inferred.get(playoff_start + 2, [])).issubset(set(observed.get(playoff_start + 2, []))),
    }
    if not all(checks.values()):
        raise ah.AlternateHistoryError(f"Historical playoff routing validation failed: {checks}")

    alternate_bracket = championship_bracket(alternate_seeded, rows, overrides, playoff_start)
    _, user_to_roster = owner_maps()
    observed_draft = following_draft_order_raw(season, user_to_roster)
    actual_expected_draft = expected_draft_order(actual_seeded, actual_bracket)
    draft_rule_validated = bool(observed_draft) and observed_draft == actual_expected_draft
    alternate_draft = expected_draft_order(alternate_seeded, alternate_bracket) if draft_rule_validated else None

    focus = str(direct.get("focus_roster_id"))
    actual_seed = next((x["seed"] for x in actual_seeded if x["roster_id"] == focus), None)
    alternate_seed = next((x["seed"] for x in alternate_seeded if x["roster_id"] == focus), None)
    actual_finish = actual_bracket["finish_by_roster"].get(focus)
    alternate_finish = alternate_bracket["finish_by_roster"].get(focus)

    report = {
        "model_version": "Fantasy-Alternate-History-0.4-postseason-raw-history",
        "scenario_id": direct.get("scenario_id"),
        "season": season,
        "focus_roster_id": focus,
        "historical_bracket_validation": {"status": "PASS", "checks": checks},
        "actual": {
            "standings": actual_seeded,
            "playoffs": actual_bracket,
            "focus_seed": actual_seed,
            "focus_finish": actual_finish,
            "following_draft_order_observed": observed_draft,
            "following_draft_order_expected": actual_expected_draft,
        },
        "alternate": {
            "standings": alternate_seeded,
            "playoffs": alternate_bracket,
            "focus_seed": alternate_seed,
            "focus_finish": alternate_finish,
            "following_draft_order": alternate_draft,
        },
        "draft_order_inference": {
            "raw_history_available": bool(observed_draft),
            "validated": draft_rule_validated,
            "method": "reverse_regular_season_for_nonplayoff_then_reverse_playoff_finish" if draft_rule_validated else None,
            "note": None if draft_rule_validated else "Raw following-year draft order does not exactly match the candidate rule; alternate exact slots remain withheld pending rule inference.",
        },
        "focus_deltas": {
            "seed_change": int(alternate_seed) - int(actual_seed) if actual_seed and alternate_seed else None,
            "playoff_finish_change": int(alternate_finish) - int(actual_finish) if actual_finish and alternate_finish else None,
            "draft_slot_actual": (observed_draft or {}).get(focus),
            "draft_slot_alternate": (alternate_draft or {}).get(focus) if alternate_draft else None,
        },
    }
    return ah.write_isolated_json(f"results/{direct.get('scenario_id')}/postseason_0_4_v3.json", report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps({
        "actual_seed": report["actual"]["focus_seed"],
        "alternate_seed": report["alternate"]["focus_seed"],
        "actual_finish": report["actual"]["focus_finish"],
        "alternate_finish": report["alternate"]["focus_finish"],
        "actual_champion": report["actual"]["playoffs"]["championship"]["winner"],
        "alternate_champion": report["alternate"]["playoffs"]["championship"]["winner"],
        "raw_draft_order_available": report["draft_order_inference"]["raw_history_available"],
        "draft_rule_validated": report["draft_order_inference"]["validated"],
        "actual_draft_slot": report["focus_deltas"]["draft_slot_actual"],
        "alternate_draft_slot": report["focus_deltas"]["draft_slot_alternate"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
