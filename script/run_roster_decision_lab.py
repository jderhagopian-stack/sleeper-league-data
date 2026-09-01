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
import types
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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    # Canonical baseline remains the native Simulator projection input.
    projections = load_json(DATA / "simulator" / season / "inputs" / "player_weekly_projections.json", {}) or {}
    raw_schedule = load_json(resolve_schedule_path(season), {}) or {}
    validation = simmod.core.validate_inputs(league, rosters, users, players, raw_schedule, projections)
    if not validation.get("validation_passed"):
        raise RuntimeError(f"Decision Lab simulator validation failed: {validation}")
    return simmod, league, rosters, users, players, season, projections, raw_schedule


def augment_projections_for_actions(actions, projections, season):
    """Add only missing transaction-player profiles from the canonical full universe.

    This preserves the native Simulator baseline and avoids changing unrelated
    rostered players merely because a hypothetical is being evaluated.
    """
    required = required_incoming_projection_ids(actions)
    native_ids = {str(x) for x in ((projections or {}).get("players") or {})}
    missing = [pid for pid in required if pid not in native_ids]
    if not missing:
        out = copy.deepcopy(projections)
        out["_decision_lab_projection_augmentation"] = {
            "source_model": None,
            "added_player_ids": [],
            "native_player_count": len(native_ids),
            "final_player_count": len(native_ids),
        }
        return out

    full_path = DATA / "simulator" / str(season) / "inputs" / "player_weekly_projections_full.json"
    full = load_json(full_path, {}) or {}
    full_players = full.get("players") or {}
    unavailable = [pid for pid in missing if str(pid) not in full_players]
    if unavailable:
        raise RuntimeError(
            "Decision scenario contains transaction players without native or canonical full "
            f"Simulator projection coverage: {unavailable}"
        )

    out = copy.deepcopy(projections)
    out.setdefault("players", {})
    for pid in missing:
        out["players"][str(pid)] = copy.deepcopy(full_players[str(pid)])
    out["_decision_lab_projection_augmentation"] = {
        "source_model": full.get("model_version"),
        "path": str(full_path),
        "added_player_ids": [str(x) for x in missing],
        "native_player_count": len(native_ids),
        "final_player_count": len(out.get("players") or {}),
        "unrelated_full_universe_players_added": False,
    }
    return out


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


def simulate_from_lineups(simmod, league, rosters, users, raw_schedule, lineups, n_sims, seed,
                          projections_override=None):
    """Run current vectorized Simulator mechanics from prepared lineups.

    Hypothetical callers may pass the exact projection universe used for lineup
    optimization. This is required for full-universe waiver/depth candidates so
    simulation-time backup chains cannot silently fall back to the narrower
    native projection set.
    """
    season = str(league.get("season"))
    projections = projections_override
    if projections is None:
        projections = load_json(DATA / "simulator" / season / "inputs" / "player_weekly_projections.json", {}) or {}
    players = load_json(DATA / "players.json", {}) or {}
    result = simmod.run_preproduction_simulation(
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
    result.setdefault("features", {})["decision_lab_projection_override_used"] = projections_override is not None
    result["features"]["decision_lab_projection_player_count"] = len((projections or {}).get("players") or {})
    aug = (projections or {}).get("_decision_lab_projection_augmentation") or {}
    result["features"]["decision_lab_projection_added_player_ids"] = list(aug.get("added_player_ids") or [])
    result["features"]["decision_lab_unrelated_full_universe_players_added"] = bool(
        aug.get("unrelated_full_universe_players_added", False)
    )
    return result

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


def required_incoming_projection_ids(actions, focus_uid=None):
    required = set()
    focus_uid = str(focus_uid) if focus_uid is not None else None
    for action in actions:
        typ = str(action.get("type") or "").lower().strip()
        if typ == "add":
            required.update(iter_player_ids(action))
        elif typ == "add_drop":
            required.update(str(x) for x in (action.get("add_players") or []))
        elif typ == "trade":
            # Every transferred player can affect a touched team's lineup.
            required.update(iter_player_ids(action))
    return sorted(required)


def assert_projection_coverage(actions, projections, focus_uid=None):
    available = {str(x) for x in ((projections or {}).get("players") or {})}
    required = required_incoming_projection_ids(actions, focus_uid)
    missing = [pid for pid in required if pid not in available]
    if missing:
        raise RuntimeError(
            "Decision scenario contains incoming/transferred players without canonical "
            f"Simulator projection coverage: {missing}"
        )
    return {
        "required_player_count": len(required),
        "missing_player_ids": missing,
        "coverage_complete": True,
        "projection_augmentation": (projections or {}).get("_decision_lab_projection_augmentation"),
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

    simmod, league, canonical_rosters, users, players, season, native_projections, raw_schedule = load_model_inputs()
    projections = augment_projections_for_actions(actions, native_projections, season)
    projection_coverage = assert_projection_coverage(actions, projections, focus_uid)
    globals_module = types.SimpleNamespace(
        roster_maps=roster_maps,
        gm_asset_map=gm_asset_map,
        remove_player=remove_player,
    )
    requested_actions = list(actions)
    pre_resolution_rosters, pick_transfers = apply_actions(canonical_rosters, requested_actions)
    touched = touched_users(focus_uid, requested_actions)

    rosteraware = load_module(Path("script/roster_aware_trade.py"), "roster_decision_roster_resolution")
    hypothetical_rosters, roster_resolution, cut_actions = rosteraware.legalize_trade_rosters(
        globals_module,
        canonical_rosters,
        pre_resolution_rosters,
        touched,
        league,
        players,
    )
    actions = requested_actions + list(cut_actions)

    baseline_lineups = load_cached_lineups(season)
    hypothetical_lineups, reoptimized_rids = reoptimize_touched_lineups(
        simmod, baseline_lineups, hypothetical_rosters, touched, league, users, players, projections
    )

    baseline = simulate_from_lineups(simmod, league, canonical_rosters, users, raw_schedule,
                                     baseline_lineups, args.sims, args.seed)
    hypothetical = simulate_from_lineups(simmod, league, hypothetical_rosters, users, raw_schedule,
                                         hypothetical_lineups, args.sims, args.seed,
                                         projections_override=projections)
    base_by_uid, hyp_by_uid = team_index(baseline), team_index(hypothetical)

    # Standalone roster/multi-move decisions must consume the same governed
    # state-aware GM3 strategic summary used by Trade Decision and Team
    # Improvement. The base module functions remain available for compatibility.
    state_aware = load_module(Path("script/decision_lab_state_aware.py"), "roster_decision_state_aware")
    strategic_runtime = types.SimpleNamespace(
        strategic_summary=strategic_summary,
        action_assets_for_user=action_assets_for_user,
    )
    state_aware.install(strategic_runtime)

    baseline_teams = list((baseline or {}).get("teams") or [])
    def mean_metric(key):
        vals = [float(x.get(key) or 0.0) for x in baseline_teams]
        return (sum(vals) / len(vals)) if vals else 0.0
    league_reference = {
        "team_count": len(baseline_teams),
        "expected_wins_mean": mean_metric("expected_wins"),
        "expected_points_for_mean": mean_metric("expected_points_for"),
        "playoff_probability_mean": mean_metric("playoff_probability"),
        "championship_probability_mean": mean_metric("championship_probability"),
        "source": "canonical_baseline_simulator_league_mean",
    }

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
            "strategic": strategic_runtime.strategic_summary(uid, actions),
        }

    focus_cmp = comparisons.get(focus_uid) or {}
    focus_title_delta = float(((focus_cmp.get("delta") or {}).get("championship_probability")) or 0.0)
    opponent_title_gain = sum(
        max(0.0, float(((row.get("delta") or {}).get("championship_probability")) or 0.0))
        for uid, row in comparisons.items() if uid != focus_uid
    )
    team_state = str((franchise_index().get(focus_uid) or {}).get("team_state") or "unknown")

    attribution_mod = load_module(Path("script/decision_attribution.py"), "roster_decision_attribution")
    utility_mod = load_module(Path("script/decision_utility.py"), "roster_decision_utility")
    attribution_by_user = {}
    for uid, cmp in comparisons.items():
        own_delta = cmp.get("delta") or {}
        other_title_gain = sum(
            max(0.0, float(((other.get("delta") or {}).get("championship_probability")) or 0.0))
            for other_uid, other in comparisons.items() if str(other_uid) != str(uid)
        )
        own_title_delta = float(own_delta.get("championship_probability") or 0.0)
        sim_view = {
            "focus_before": cmp.get("before"),
            "focus_after": cmp.get("after"),
            "focus_delta": {
                k: own_delta.get(k)
                for k in ("expected_wins", "expected_points_for", "playoff_probability", "bye_probability", "championship_probability")
            },
            "league_reference": league_reference,
            "strategic": cmp.get("strategic") or {},
            "buyer_championship_probability_delta": round(other_title_gain, 5),
            "net_title_equity_swing_against_focus": round(other_title_gain - own_title_delta, 5),
            "competitive_externality": {
                "focus_championship_probability_delta": round(own_title_delta, 5),
                "opponent_positive_championship_probability_delta_sum": round(other_title_gain, 5),
                "net_title_equity_swing_against_focus": round(other_title_gain - own_title_delta, 5),
            },
        }
        attribution_by_user[str(uid)] = attribution_mod.reconcile(sim_view)

    focal_attribution = attribution_by_user.get(focus_uid) or {}
    focal_score = float(focal_attribution.get("final_shared_decision_utility") or 0.0)
    if focal_score > 0:
        authoritative_band = "IMPROVES_FRANCHISE"
    elif focal_score < 0:
        authoritative_band = "HARMS_FRANCHISE"
    else:
        authoritative_band = "NEUTRAL"

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
        "requested_actions": requested_actions,
        "actions": actions,
        "automatic_roster_cut_actions": cut_actions,
        "roster_resolution": roster_resolution,
        "roster_resolution_model_version": getattr(rosteraware, "MODEL_VERSION", None),
        "ephemeral_state": True,
        "canonical_state_mutated": False,
        "pick_transfers": pick_transfers,
        "projection_input_coverage": projection_coverage,
        "team_comparisons": comparisons,
        "competitive_externality": {
            "focus_championship_probability_delta": round(focus_title_delta, 5),
            "opponent_positive_championship_probability_delta_sum": round(opponent_title_gain, 5),
            "net_title_equity_swing_against_focus": round(opponent_title_gain - focus_title_delta, 5),
        },
        "decision_attribution_by_user": attribution_by_user,
        "decision_attribution": focal_attribution,
        "shared_decision_utility_score": focal_score,
        "shared_decision_utility_model_version": utility_mod.MODEL_VERSION,
        "recommendation": {
            "band": authoritative_band,
            "authority": "Shared Decision Utility / GM3 Team Improvement",
            "shared_decision_utility_score": focal_score,
            "pareto_diagnostic": classify_decision(focus_cmp, team_state),
            "no_independent_roster_decision_score_created": True,
        },
    }

    output = Path(args.output) if args.output else DATA / "decision_lab" / "outputs" / f"{report['scenario_id']}.json"
    write_json(output, report)
    print(json.dumps(report, indent=2))
    print(f"\nWrote Decision Lab report: {output}")


if __name__ == "__main__":
    main()
