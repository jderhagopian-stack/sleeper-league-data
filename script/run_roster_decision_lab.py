#!/usr/bin/env python3
"""FSFFL Roster Decision Lab 1.1.

Fast ephemeral what-if engine for trades, adds, drops/cuts, and multi-step
roster moves. Canonical Sleeper/GM state is read-only.

Performance design:
- Rebuild baseline lineups with the current canonical Simulator optimizer on every run; the full 12-team lineup build is fast enough that persisted-cache drift is not justified.
- Re-optimize only teams touched by the hypothetical decision.
- Run paired Monte Carlo from prepared lineups for fast decision deltas.
- Use the same simulation seed for baseline and hypothetical worlds.
- Default to 50,000 paired simulations for final decision support.

Decision Lab uses the current vectorized Simulator implementation for paired hypothetical runs. Prepared lineups allow only touched teams to be re-optimized while scoring, correlation, bench substitution, playoff mechanics and RNG behavior remain aligned with the canonical Simulator.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DATA = Path("data")
MODEL_VERSION = "FSFFL-Roster-Decision-Lab-1.1"
DEFAULT_SIMS = 50000
DEFAULT_SEED = 20260821


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def import_simulator():
    path = Path("script/run_fsffl_season_simulator_preproduction.py")
    script_dir = str(path.resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("fsffl_simulator_current", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import simulator from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def roster_maps(rosters: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    by_uid, by_rid = {}, {}
    for r in rosters:
        by_uid[str(r.get("owner_id"))] = r
        by_rid[int(r.get("roster_id"))] = r
    return by_uid, by_rid


def normalize_players(roster: Dict[str, Any]):
    for key in ("players", "taxi", "reserve"):
        roster[key] = [str(x) for x in (roster.get(key) or [])]


def player_owner_map(rosters: List[Dict[str, Any]]) -> Dict[str, str]:
    out = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        normalize_players(r)
        for pid in r.get("players") or []:
            out[str(pid)] = uid
    return out


def add_player(roster: Dict[str, Any], pid: str):
    normalize_players(roster)
    pid = str(pid)
    if pid not in roster["players"]:
        roster["players"].append(pid)


def remove_player(roster: Dict[str, Any], pid: str):
    normalize_players(roster)
    pid = str(pid)
    roster["players"] = [x for x in roster["players"] if x != pid]
    roster["taxi"] = [x for x in roster["taxi"] if x != pid]
    roster["reserve"] = [x for x in roster["reserve"] if x != pid]


def ensure_user(by_uid, uid, idx):
    if uid not in by_uid:
        raise ValueError(f"Action {idx}: unknown user id {uid}")


def ensure_owned(owners, uid, pid, idx):
    actual = owners.get(str(pid))
    if actual != str(uid):
        raise ValueError(f"Action {idx}: player {pid} is not owned by {uid}; current owner={actual}")


def ensure_unowned(owners, pid, idx):
    actual = owners.get(str(pid))
    if actual is not None:
        raise ValueError(f"Action {idx}: add target {pid} is already rostered by {actual}")


def iter_player_ids(action: Dict[str, Any], plural_key="players", singular_key="player_id") -> Iterable[str]:
    vals = action.get(plural_key)
    if vals is None and action.get(singular_key) is not None:
        vals = [action.get(singular_key)]
    return [str(x) for x in (vals or [])]


def apply_actions(rosters: List[Dict[str, Any]], actions: List[Dict[str, Any]]):
    out = copy.deepcopy(rosters)
    by_uid, _ = roster_maps(out)
    owners = player_owner_map(out)
    pick_transfers = []

    for idx, action in enumerate(actions, start=1):
        typ = str(action.get("type") or "").lower().strip()
        if typ == "trade":
            src, dst = str(action.get("from_user_id")), str(action.get("to_user_id"))
            ensure_user(by_uid, src, idx)
            ensure_user(by_uid, dst, idx)
            if src == dst:
                raise ValueError(f"Action {idx}: trade source and destination are identical")
            for pid in iter_player_ids(action):
                ensure_owned(owners, src, pid, idx)
                remove_player(by_uid[src], pid)
                add_player(by_uid[dst], pid)
                owners[pid] = dst
            for pick in action.get("picks") or []:
                pick_transfers.append({"asset_id": str(pick), "from_user_id": src, "to_user_id": dst})
        elif typ in {"drop", "cut"}:
            uid = str(action.get("user_id"))
            ensure_user(by_uid, uid, idx)
            for pid in iter_player_ids(action):
                ensure_owned(owners, uid, pid, idx)
                remove_player(by_uid[uid], pid)
                owners.pop(pid, None)
        elif typ == "add":
            uid = str(action.get("user_id"))
            ensure_user(by_uid, uid, idx)
            for pid in iter_player_ids(action):
                ensure_unowned(owners, pid, idx)
                add_player(by_uid[uid], pid)
                owners[pid] = uid
        elif typ == "add_drop":
            uid = str(action.get("user_id"))
            ensure_user(by_uid, uid, idx)
            drops = [str(x) for x in (action.get("drop_players") or [])]
            adds = [str(x) for x in (action.get("add_players") or [])]
            for pid in drops:
                ensure_owned(owners, uid, pid, idx)
                remove_player(by_uid[uid], pid)
                owners.pop(pid, None)
            for pid in adds:
                ensure_unowned(owners, pid, idx)
                add_player(by_uid[uid], pid)
                owners[pid] = uid
        else:
            raise ValueError(f"Action {idx}: unsupported action type {typ!r}")
    return out, pick_transfers


def touched_users(focus_uid: str, actions: List[Dict[str, Any]]) -> List[str]:
    touched = {str(focus_uid)}
    for a in actions:
        for key in ("user_id", "from_user_id", "to_user_id"):
            if a.get(key) is not None:
                touched.add(str(a.get(key)))
    return sorted(touched)


def team_index(sim: Dict[str, Any]):
    return {str(t.get("user_id")): t for t in sim.get("teams") or []}


def delta(before, after):
    if before is None or after is None:
        return None
    return round(float(after) - float(before), 5)


def asset_value_maps():
    asset_file = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    players, picks = {}, {}
    for p in asset_file.get("players") or []:
        players[f"player:{p.get('player_id')}"] = {
            "name": p.get("name"),
            "dynasty": float(p.get("market_dynasty") or 0.0),
            "redraft": float(p.get("market_redraft") or 0.0),
            "fsffl": float(p.get("fsffl_value") or 0.0),
        }
    for p in asset_file.get("picks") or []:
        aid = p.get("asset_id")
        if aid:
            picks[str(aid)] = {
                "name": p.get("name") or aid,
                "dynasty": float(p.get("market_dynasty") or p.get("fsffl_value") or 0.0),
                "redraft": 0.0,
                "fsffl": float(p.get("fsffl_value") or p.get("market_dynasty") or 0.0),
            }
    return players, picks


def franchise_index():
    idx = load_json(DATA / "gm" / "franchise_index.json", {}) or {}
    return {str(x.get("user_id")): x for x in idx.get("teams") or []}


def gm_profile_for_user(uid: str):
    row = franchise_index().get(str(uid))
    path = ((row or {}).get("paths") or {}).get("strategic_asset_profiles")
    return load_json(Path(path), {}) if path else {}


def gm_asset_map(uid: str):
    return {str(a.get("asset_id")): a for a in (gm_profile_for_user(uid) or {}).get("assets") or []}


def action_assets_for_user(actions, uid):
    sent, received = [], []
    uid = str(uid)
    for action in actions:
        typ = str(action.get("type") or "").lower()
        if typ == "trade":
            assets = [f"player:{x}" for x in iter_player_ids(action)] + [str(x) for x in (action.get("picks") or [])]
            if str(action.get("from_user_id")) == uid:
                sent.extend(assets)
            if str(action.get("to_user_id")) == uid:
                received.extend(assets)
        elif typ in {"drop", "cut"} and str(action.get("user_id")) == uid:
            sent.extend(f"player:{x}" for x in iter_player_ids(action))
        elif typ == "add" and str(action.get("user_id")) == uid:
            received.extend(f"player:{x}" for x in iter_player_ids(action))
        elif typ == "add_drop" and str(action.get("user_id")) == uid:
            sent.extend(f"player:{x}" for x in (action.get("drop_players") or []))
            received.extend(f"player:{x}" for x in (action.get("add_players") or []))
    return sent, received


def strategic_summary_from_maps(uid, actions, gm, players, picks):
    """Canonical GM 3.0 strategic-summary calculation with injectable value maps.

    Current analysis calls this with live GM/market maps. Historical analysis may
    call the exact same calculation with time-frozen maps, preventing a second
    historical scoring formula from drifting away from GM 3.0.
    """
    sent, received = action_assets_for_user(actions, uid)

    def val(aid):
        g = gm.get(aid) or {}
        m = players.get(aid) or picks.get(aid) or {}
        base = float(g.get("base_franchise_value") or m.get("fsffl") or m.get("dynasty") or 0.0)
        return {
            "asset_id": aid,
            "name": g.get("name") or m.get("name") or aid,
            "market_dynasty": float(g.get("market_dynasty") or m.get("dynasty") or 0.0),
            "market_redraft": float(g.get("market_redraft") or m.get("redraft") or 0.0),
            "base_franchise_value": base,
            "break_glass_value": float(g.get("break_glass_value") or base),
            "core_status": g.get("core_status"),
        }

    sent_rows, rec_rows = [val(x) for x in sent], [val(x) for x in received]
    total = lambda rows, key: sum(float(x.get(key) or 0.0) for x in rows)
    return {
        "sent": sent_rows,
        "received": rec_rows,
        "market_dynasty_delta": round(total(rec_rows, "market_dynasty") - total(sent_rows, "market_dynasty"), 2),
        "market_redraft_delta": round(total(rec_rows, "market_redraft") - total(sent_rows, "market_redraft"), 2),
        "base_franchise_value_delta": round(total(rec_rows, "base_franchise_value") - total(sent_rows, "base_franchise_value"), 2),
        "break_glass_delta": round(total(rec_rows, "break_glass_value") - total(sent_rows, "break_glass_value"), 2),
    }


def strategic_summary(uid, actions):
    gm = gm_asset_map(uid)
    players, picks = asset_value_maps()
    return strategic_summary_from_maps(uid, actions, gm, players, picks)


def resolve_schedule_path(season: str) -> Path:
    candidates = [
        DATA / "stats" / "fsffl" / season / "league_matchups_raw.json",
        DATA / "simulator" / season / "inputs" / "fsffl_schedule.json",
        DATA / "simulator" / season / "inputs" / "schedule.json",
        DATA / "schedule.json",
        DATA / "matchups.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("Could not locate canonical FSFFL schedule input")


def load_model_inputs():
    simmod = import_simulator()
    league = load_json(DATA / "league.json", {}) or {}
    rosters = load_json(DATA / "rosters.json", []) or []
    users = load_json(DATA / "users.json", []) or []
    players = load_json(DATA / "players.json", {}) or {}
    season = str(league.get("season"))
    projections = load_json(DATA / "simulator" / season / "inputs" / "player_weekly_projections.json", {}) or {}
    raw_schedule = load_json(resolve_schedule_path(season), {}) or {}
    validation = simmod.core.validate_inputs(league, rosters, users, players, raw_schedule, projections)
    if not validation.get("validation_passed"):
        raise RuntimeError(f"Decision Lab simulator validation failed: {validation}")
    return simmod, league, rosters, users, players, season, projections, raw_schedule


def load_cached_lineups(season: str) -> Dict[int, Dict[int, List[Dict[str, Any]]]]:
    """Compatibility facade that rebuilds the canonical baseline lineups fresh.

    Historical Decision Lab versions reused weekly_optimized_lineups.json.
    The current vectorized Simulator can rebuild all FSFFL lineups in well under
    a second, so fresh construction is preferred over any persisted-cache drift.
    This guarantees the same optimizer and runtime metadata used by the
    authoritative Simulator while preserving the existing caller API.
    """
    simmod = import_simulator()
    league = load_json(DATA / "league.json", {}) or {}
    rosters = load_json(DATA / "rosters.json", []) or []
    players = load_json(DATA / "players.json", {}) or {}
    projections = load_json(
        DATA / "simulator" / season / "inputs" / "player_weekly_projections.json",
        {},
    ) or {}
    reg_weeks = simmod.core.regular_season_weeks(league)
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    all_weeks = sorted(set(reg_weeks + [playoff_start, playoff_start + 1, playoff_start + 2]))
    _, by_rid = roster_maps(rosters)
    return {
        rid: {
            week: simmod.optimize_fsffl_fast(
                roster, week, league, players, projections
            )
            for week in all_weeks
        }
        for rid, roster in by_rid.items()
    }

def reoptimize_touched_lineups(simmod, baseline_lineups, hypothetical_rosters, touched_uids,
                               league, users, players, projections):
    lineups = copy.deepcopy(baseline_lineups)
    by_uid, _ = roster_maps(hypothetical_rosters)
    reg_weeks = simmod.core.regular_season_weeks(league)
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    all_weeks = sorted(set(reg_weeks + [playoff_start, playoff_start + 1, playoff_start + 2]))
    reoptimized = []
    for uid in touched_uids:
        roster = by_uid.get(str(uid))
        if not roster:
            continue
        rid = int(roster.get("roster_id"))
        lineups[rid] = {}
        for week in all_weeks:
            lineups[rid][week] = simmod.optimize_fsffl_fast(roster, week, league, players, projections)
        reoptimized.append(rid)
    return lineups, reoptimized


def simulate_from_lineups(simmod, league, rosters, users, raw_schedule, lineups, n_sims, seed):
    """Run current vectorized Simulator mechanics from prepared lineups."""
    season = str(league.get("season"))
    projections = load_json(DATA / "simulator" / season / "inputs" / "player_weekly_projections.json", {}) or {}
    players = load_json(DATA / "players.json", {}) or {}
    return simmod.run_preproduction_simulation(
        league,
        rosters,
        users,
        players,
        raw_schedule,
        projections,
        n_sims=n_sims,
        seed=seed,
        lineups_override=lineups,
    )

def classify_decision(focus_cmp: Dict[str, Any], team_state: str):
    """Threshold-free legacy Decision Lab classification.

    This path is not the production Trade Decision authority, but it should not
    preserve obsolete categorical contender/rebuild cliffs. Use Pareto
    direction across directly interpretable competitive outcomes plus market
    dynasty value. Mixed tradeoffs are explicitly left for the canonical
    downstream utility/context rather than forced through arbitrary cutoffs.
    """
    d = focus_cmp.get("delta") or {}
    s = focus_cmp.get("strategic") or {}
    metrics = {
        "expected_points_for": float(d.get("expected_points_for") or 0.0),
        "expected_wins": float(d.get("expected_wins") or 0.0),
        "playoff_probability": float(d.get("playoff_probability") or 0.0),
        "championship_probability": float(d.get("championship_probability") or 0.0),
        "market_dynasty_delta": float(s.get("market_dynasty_delta") or 0.0),
    }
    any_positive = any(v > 0 for v in metrics.values())
    any_negative = any(v < 0 for v in metrics.values())
    if any_positive and not any_negative:
        band = "accept_pareto_improvement"
    elif any_negative and not any_positive:
        band = "reject_pareto_deterioration"
    elif not any_positive and not any_negative:
        band = "equivalent"
    else:
        band = "needs_context"
    return {
        "band": band,
        "team_state": team_state,
        "team_state_is_descriptive_only": True,
        "rule_based": False,
        "comparison_basis": "threshold_free_pareto_direction",
        "metrics": metrics,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    if args.sims < 100:
        raise ValueError("--sims must be at least 100")

    scenario_path = Path(args.scenario)
    scenario = load_json(scenario_path, {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    actions = scenario.get("actions") or []
    if not focus_uid or not actions:
        raise ValueError("scenario.focus_user_id and scenario.actions are required")

    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = load_model_inputs()
    hypothetical_rosters, pick_transfers = apply_actions(canonical_rosters, actions)
    touched = touched_users(focus_uid, actions)

    baseline_lineups = load_cached_lineups(season)
    hypothetical_lineups, reoptimized_rids = reoptimize_touched_lineups(
        simmod, baseline_lineups, hypothetical_rosters, touched, league, users, players, projections
    )

    baseline = simulate_from_lineups(simmod, league, canonical_rosters, users, raw_schedule,
                                     baseline_lineups, args.sims, args.seed)
    hypothetical = simulate_from_lineups(simmod, league, hypothetical_rosters, users, raw_schedule,
                                         hypothetical_lineups, args.sims, args.seed)
    base_by_uid, hyp_by_uid = team_index(baseline), team_index(hypothetical)

    comparisons = {}
    for uid in touched:
        b, h = base_by_uid.get(uid), hyp_by_uid.get(uid)
        if not b or not h:
            continue
        comparisons[uid] = {
            "manager": b.get("manager"),
            "team_name": b.get("team_name"),
            "before": b,
            "after": h,
            "delta": {
                "expected_wins": delta(b.get("expected_wins"), h.get("expected_wins")),
                "expected_points_for": delta(b.get("expected_points_for"), h.get("expected_points_for")),
                "playoff_probability": delta(b.get("playoff_probability"), h.get("playoff_probability")),
                "bye_probability": delta(b.get("bye_probability"), h.get("bye_probability")),
                "division_probability": delta(b.get("division_probability"), h.get("division_probability")),
                "championship_probability": delta(b.get("championship_probability"), h.get("championship_probability")),
            },
            "strategic": strategic_summary(uid, actions),
        }

    focus_cmp = comparisons.get(focus_uid) or {}
    focus_title_delta = float(((focus_cmp.get("delta") or {}).get("championship_probability")) or 0.0)
    opponent_title_gain = sum(
        max(0.0, float(((row.get("delta") or {}).get("championship_probability")) or 0.0))
        for uid, row in comparisons.items() if uid != focus_uid
    )
    team_state = str((franchise_index().get(focus_uid) or {}).get("team_state") or "unknown")

    report = {
        "model_version": MODEL_VERSION,
        "scenario_id": scenario.get("scenario_id") or scenario_path.stem,
        "description": scenario.get("description"),
        "focus_user_id": focus_uid,
        "simulation": {
            "n_sims": args.sims,
            "seed": args.seed,
            "common_random_numbers": True,
            "simulator_model_version": baseline.get("model_version"),
            "execution_path": "cached_lineups_plus_touched_team_reoptimization",
            "teams_reoptimized": reoptimized_rids,
            "default_latency_target": "under_2_minutes",
        },
        "actions": actions,
        "ephemeral_state": True,
        "canonical_state_mutated": False,
        "pick_transfers": pick_transfers,
        "team_comparisons": comparisons,
        "competitive_externality": {
            "focus_championship_probability_delta": round(focus_title_delta, 5),
            "opponent_positive_championship_probability_delta_sum": round(opponent_title_gain, 5),
            "net_title_equity_swing_against_focus": round(opponent_title_gain - focus_title_delta, 5),
        },
        "recommendation": classify_decision(focus_cmp, team_state),
    }

    output = Path(args.output) if args.output else DATA / "decision_lab" / "outputs" / f"{report['scenario_id']}.json"
    write_json(output, report)
    print(json.dumps(report, indent=2))
    print(f"\nWrote Decision Lab report: {output}")


if __name__ == "__main__":
    main()
