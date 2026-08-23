#!/usr/bin/env python3
"""FSFFL Alternate History 0.4: postseason and draft-order consequences.

Consumes a 0.3 direct historical replay. Completed NFL/fantasy scoring remains
immutable. The only changes are fantasy ownership, pre-week lineup choices,
regular-season results, playoff seeding/opponents, and downstream rookie-draft
position when the historical draft-order rule can be inferred and validated.

Safety/accuracy rules:
- never simulate or alter completed NFL outcomes;
- use each roster's recorded weekly fantasy points unless 0.3 produced an
  audited counterfactual score for that roster/week;
- infer the championship-bracket routing from actual FSFFL history and fail if
  the observed bracket does not match the inferred six-team structure;
- infer the rookie draft-order rule from the actual following-year draft and
  report unsupported rather than inventing a rule when validation fails.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import alternate_history_engine as ah
from run_fsffl_counterfactual_replay import run as run_direct

DATA = Path("data")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def owner_maps() -> Tuple[Dict[str, str], Dict[str, str]]:
    roster_to_user: Dict[str, str] = {}
    user_to_roster: Dict[str, str] = {}
    for row in load(DATA / "rosters.json"):
        rid = str(row.get("roster_id"))
        uid = str(row.get("owner_id"))
        if rid and uid and uid != "None":
            roster_to_user[rid] = uid
            user_to_roster[uid] = rid
    return roster_to_user, user_to_roster


def weekly_rows(matchups: Dict[str, List[Dict[str, Any]]]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    out: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for week_key, rows in matchups.items():
        out[int(week_key)] = {str(r.get("roster_id")): r for r in rows}
    return out


def score_override_index(report: Dict[str, Any]) -> Dict[Tuple[int, str], float]:
    out: Dict[Tuple[int, str], float] = {}
    for week_row in report.get("weekly_lineup_changes") or []:
        week = int(week_row.get("week"))
        for row in week_row.get("affected_rosters") or []:
            out[(week, str(row.get("roster_id")))] = float(row.get("alternate_points") or 0.0)
    return out


def team_score(
    week_rows: Dict[int, Dict[str, Dict[str, Any]]],
    overrides: Dict[Tuple[int, str], float],
    week: int,
    roster_id: str,
) -> float:
    key = (int(week), str(roster_id))
    if key in overrides:
        return round(float(overrides[key]), 2)
    row = (week_rows.get(int(week)) or {}).get(str(roster_id))
    if row is None:
        raise ah.AlternateHistoryError(f"Missing historical matchup row for roster {roster_id}, week {week}")
    return round(float(row.get("points") or 0.0), 2)


def standings(records: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for rid, rec in records.items():
        rows.append(
            {
                "roster_id": str(rid),
                "wins": int(rec.get("wins") or 0),
                "losses": int(rec.get("losses") or 0),
                "ties": int(rec.get("ties") or 0),
                "points_for": round(float(rec.get("points_for") or 0.0), 2),
            }
        )
    rows.sort(
        key=lambda x: (
            x["wins"] + 0.5 * x["ties"],
            x["points_for"],
            -int(x["roster_id"]),
        ),
        reverse=True,
    )
    for idx, row in enumerate(rows, 1):
        row["seed"] = idx
    return rows


def play_game(
    week_rows: Dict[int, Dict[str, Dict[str, Any]]],
    overrides: Dict[Tuple[int, str], float],
    week: int,
    a: str,
    b: str,
    seed_by_roster: Dict[str, int],
) -> Dict[str, Any]:
    pa = team_score(week_rows, overrides, week, a)
    pb = team_score(week_rows, overrides, week, b)
    if abs(pa - pb) <= 0.0001:
        # Sleeper playoff ties advance the higher seed. Preserve a deterministic
        # league-rule interpretation and surface it in the audit.
        winner = a if seed_by_roster[a] < seed_by_roster[b] else b
        tiebreak = "higher_seed"
    else:
        winner = a if pa > pb else b
        tiebreak = None
    loser = b if winner == a else a
    return {
        "week": week,
        "team_a": a,
        "team_b": b,
        "team_a_points": pa,
        "team_b_points": pb,
        "winner": winner,
        "loser": loser,
        "tiebreak": tiebreak,
    }


def championship_bracket(
    seeded: List[Dict[str, Any]],
    week_rows: Dict[int, Dict[str, Dict[str, Any]]],
    overrides: Dict[Tuple[int, str], float],
    playoff_start: int,
) -> Dict[str, Any]:
    if len(seeded) < 6:
        raise ah.AlternateHistoryError("Six-team playoff bracket requires at least six standings rows")
    top6 = seeded[:6]
    seed_to_roster = {int(x["seed"]): str(x["roster_id"]) for x in top6}
    seed_by_roster = {v: k for k, v in seed_to_roster.items()}

    # Standard six-team Sleeper bracket. This routing is separately validated
    # against the actual recorded postseason before alternate results are trusted.
    qf_36 = play_game(week_rows, overrides, playoff_start, seed_to_roster[3], seed_to_roster[6], seed_by_roster)
    qf_45 = play_game(week_rows, overrides, playoff_start, seed_to_roster[4], seed_to_roster[5], seed_by_roster)
    sf_1 = play_game(week_rows, overrides, playoff_start + 1, seed_to_roster[1], qf_45["winner"], seed_by_roster)
    sf_2 = play_game(week_rows, overrides, playoff_start + 1, seed_to_roster[2], qf_36["winner"], seed_by_roster)
    final = play_game(week_rows, overrides, playoff_start + 2, sf_1["winner"], sf_2["winner"], seed_by_roster)
    third = play_game(week_rows, overrides, playoff_start + 2, sf_1["loser"], sf_2["loser"], seed_by_roster)

    # Fifth/sixth are ordered by the Week 16 score of the two quarterfinal losers
    # only if both have historical Week 16 rows; this mirrors the placement-game
    # concept without inventing an opponent. The draft-order inference below must
    # still validate against the observed following-year draft before use.
    qf_losers = [qf_36["loser"], qf_45["loser"]]
    fifth_scores = [(team_score(week_rows, overrides, playoff_start + 1, rid), rid) for rid in qf_losers]
    fifth_scores.sort(reverse=True)
    fifth, sixth = fifth_scores[0][1], fifth_scores[1][1]

    finish = {
        final["winner"]: 1,
        final["loser"]: 2,
        third["winner"]: 3,
        third["loser"]: 4,
        fifth: 5,
        sixth: 6,
    }
    return {
        "playoff_field": [{"seed": i, "roster_id": seed_to_roster[i]} for i in range(1, 7)],
        "quarterfinals": [qf_36, qf_45],
        "semifinals": [sf_1, sf_2],
        "championship": final,
        "third_place": third,
        "finish_by_roster": finish,
    }


def observed_playoff_pairs(matchups: Dict[str, List[Dict[str, Any]]], playoff_start: int, playoff_field: set[str]) -> Dict[int, List[Tuple[str, str]]]:
    out: Dict[int, List[Tuple[str, str]]] = {}
    for week in (playoff_start, playoff_start + 1, playoff_start + 2):
        groups: Dict[str, List[str]] = defaultdict(list)
        for row in matchups.get(str(week), []):
            mid = row.get("matchup_id")
            rid = str(row.get("roster_id"))
            if mid is None or rid not in playoff_field:
                continue
            groups[str(mid)].append(rid)
        pairs = []
        for vals in groups.values():
            if len(vals) == 2:
                pairs.append(tuple(sorted(vals)))
        out[week] = sorted(set(pairs))
    return out


def inferred_pairs(bracket: Dict[str, Any]) -> Dict[int, List[Tuple[str, str]]]:
    out: Dict[int, List[Tuple[str, str]]] = defaultdict(list)
    for key in ("quarterfinals", "semifinals"):
        for game in bracket[key]:
            out[int(game["week"])].append(tuple(sorted((game["team_a"], game["team_b"]))))
    for key in ("championship", "third_place"):
        game = bracket[key]
        out[int(game["week"])].append(tuple(sorted((game["team_a"], game["team_b"]))))
    return {w: sorted(set(v)) for w, v in out.items()}


def following_draft_order(season: str, user_to_roster: Dict[str, str]) -> Optional[Dict[str, int]]:
    target = str(int(season) + 1)
    for entry in load(DATA / "drafts.json"):
        draft = entry.get("draft") or {}
        if str(draft.get("season")) != target:
            continue
        raw = draft.get("draft_order") or {}
        out: Dict[str, int] = {}
        for uid, slot in raw.items():
            rid = user_to_roster.get(str(uid))
            if rid:
                out[rid] = int(slot)
        if out:
            return out
    return None


def expected_draft_order(
    seeded: List[Dict[str, Any]],
    playoff: Dict[str, Any],
) -> Dict[str, int]:
    # Common dynasty mapping: non-playoff teams draft in reverse regular-season
    # order; playoff teams draft by reverse final playoff finish (champion last).
    # This function is NEVER trusted unless it exactly reproduces the actual
    # following-year draft order for the no-fork control.
    playoff_ids = {str(x["roster_id"]) for x in playoff["playoff_field"]}
    nonplay = [x for x in seeded if str(x["roster_id"]) not in playoff_ids]
    nonplay.sort(key=lambda x: int(x["seed"]), reverse=True)
    order: List[str] = [str(x["roster_id"]) for x in nonplay]
    playoff_finish = playoff["finish_by_roster"]
    playoff_order = sorted(playoff_finish, key=lambda rid: int(playoff_finish[rid]), reverse=True)
    order.extend(playoff_order)
    return {rid: idx for idx, rid in enumerate(order, 1)}


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    season = str(payload.get("fork_season") or "")
    if not season:
        raise ah.AlternateHistoryError("Scenario requires fork_season")

    direct_path = run_direct(scenario_path)
    direct = load(direct_path)
    matchups = load(DATA / "stats" / "fsffl" / season / "league_matchups_raw.json")
    league = load(DATA / "league.json")
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    playoff_teams = int((league.get("settings") or {}).get("playoff_teams") or 6)
    if playoff_teams != 6:
        raise ah.AlternateHistoryError(f"0.4 currently validates six-team brackets; league has {playoff_teams}")

    rows = weekly_rows(matchups)
    overrides = score_override_index(direct)
    actual_seeded = standings(direct.get("actual_records") or {})
    alternate_seeded = standings(direct.get("alternate_records") or {})

    actual_bracket = championship_bracket(actual_seeded, rows, {}, playoff_start)
    actual_field = {str(x["roster_id"]) for x in actual_bracket["playoff_field"]}
    observed = observed_playoff_pairs(matchups, playoff_start, actual_field)
    inferred = inferred_pairs(actual_bracket)

    # Week 15 and semifinals must match exactly. Week 17 may contain additional
    # placement games; championship + third-place pairs are required subsets.
    bracket_checks = {
        "week15_exact": observed.get(playoff_start, []) == inferred.get(playoff_start, []),
        "week16_contains_semifinals": set(inferred.get(playoff_start + 1, [])).issubset(set(observed.get(playoff_start + 1, []))),
        "week17_contains_title_and_third": set(inferred.get(playoff_start + 2, [])).issubset(set(observed.get(playoff_start + 2, []))),
    }
    if not all(bracket_checks.values()):
        raise ah.AlternateHistoryError(f"Historical playoff routing validation failed: {bracket_checks}; observed={observed}; inferred={inferred}")

    alternate_bracket = championship_bracket(alternate_seeded, rows, overrides, playoff_start)

    roster_to_user, user_to_roster = owner_maps()
    observed_draft = following_draft_order(season, user_to_roster)
    actual_expected_draft = expected_draft_order(actual_seeded, actual_bracket)
    draft_rule_validated = bool(observed_draft) and observed_draft == actual_expected_draft
    alternate_draft = expected_draft_order(alternate_seeded, alternate_bracket) if draft_rule_validated else None

    focus = str(direct.get("focus_roster_id"))
    actual_seed = next((x["seed"] for x in actual_seeded if x["roster_id"] == focus), None)
    alternate_seed = next((x["seed"] for x in alternate_seeded if x["roster_id"] == focus), None)
    actual_finish = actual_bracket["finish_by_roster"].get(focus)
    alternate_finish = alternate_bracket["finish_by_roster"].get(focus)

    report = {
        "model_version": "Fantasy-Alternate-History-0.4-postseason",
        "scenario_id": direct.get("scenario_id"),
        "season": season,
        "focus_roster_id": focus,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "uses_0_3_audited_weekly_counterfactual_scores": True,
            "historical_bracket_routing_validated_before_counterfactual_use": True,
            "draft_order_rule_used_only_if_exactly_backvalidated": True,
        },
        "historical_bracket_validation": {
            "status": "PASS",
            "checks": bracket_checks,
            "observed_pairs": {str(k): v for k, v in observed.items()},
            "inferred_pairs": {str(k): v for k, v in inferred.items()},
        },
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
            "validated": draft_rule_validated,
            "method": "reverse_regular_season_for_nonplayoff_then_reverse_playoff_finish" if draft_rule_validated else None,
            "note": None if draft_rule_validated else "Observed following-year draft order does not exactly match the candidate rule; exact alternate draft slots are withheld.",
        },
        "focus_deltas": {
            "seed_change": None if actual_seed is None or alternate_seed is None else int(alternate_seed) - int(actual_seed),
            "playoff_finish_change": None if actual_finish is None or alternate_finish is None else int(alternate_finish) - int(actual_finish),
            "draft_slot_actual": (observed_draft or {}).get(focus),
            "draft_slot_alternate": (alternate_draft or {}).get(focus) if alternate_draft else None,
        },
    }
    out = ah.write_isolated_json(f"results/{direct.get('scenario_id')}/postseason_0_4.json", report)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.4 postseason consequences")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps({
        "scenario_id": report["scenario_id"],
        "actual_seed": report["actual"]["focus_seed"],
        "alternate_seed": report["alternate"]["focus_seed"],
        "actual_finish": report["actual"]["focus_finish"],
        "alternate_finish": report["alternate"]["focus_finish"],
        "draft_rule_validated": report["draft_order_inference"]["validated"],
        "actual_draft_slot": report["focus_deltas"]["draft_slot_actual"],
        "alternate_draft_slot": report["focus_deltas"]["draft_slot_alternate"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
