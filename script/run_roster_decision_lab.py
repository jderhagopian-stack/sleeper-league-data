#!/usr/bin/env python3
"""FSFFL Roster Decision Lab 1.0.

Ephemeral what-if engine for trades, adds, drops/cuts, and multi-step roster
moves. It reads canonical league data, applies a scenario in memory, reruns the
existing FSFFL season simulator on the hypothetical state, and writes a compact
comparison report. Canonical roster/pick state is never modified.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DATA = Path("data")
MODEL_VERSION = "FSFFL-Roster-Decision-Lab-1.0"
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
    path = Path("script/build_fsffl_season_simulator.py")
    spec = importlib.util.spec_from_file_location("fsffl_simulator", path)
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


def ensure_user(by_uid: Dict[str, Dict[str, Any]], uid: str, idx: int):
    if uid not in by_uid:
        raise ValueError(f"Action {idx}: unknown user id {uid}")


def ensure_owned(owners: Dict[str, str], uid: str, pid: str, idx: int):
    actual = owners.get(str(pid))
    if actual != str(uid):
        raise ValueError(f"Action {idx}: player {pid} is not owned by {uid}; current owner={actual}")


def ensure_unowned(owners: Dict[str, str], pid: str, idx: int):
    actual = owners.get(str(pid))
    if actual is not None:
        raise ValueError(f"Action {idx}: add target {pid} is already rostered by {actual}")


def iter_player_ids(action: Dict[str, Any], plural_key: str = "players", singular_key: str = "player_id") -> Iterable[str]:
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


def strategic_summary(uid, actions):
    sent, received = action_assets_for_user(actions, uid)
    gm = gm_asset_map(uid)
    players, picks = asset_value_maps()

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

    def total(rows, key):
        return sum(float(x.get(key) or 0.0) for x in rows)

    return {
        "sent": sent_rows,
        "received": rec_rows,
        "market_dynasty_delta": round(total(rec_rows, "market_dynasty") - total(sent_rows, "market_dynasty"), 2),
        "market_redraft_delta": round(total(rec_rows, "market_redraft") - total(sent_rows, "market_redraft"), 2),
        "base_franchise_value_delta": round(total(rec_rows, "base_franchise_value") - total(sent_rows, "base_franchise_value"), 2),
        "break_glass_delta": round(total(rec_rows, "break_glass_value") - total(sent_rows, "break_glass_value"), 2),
    }


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


def run_sims(rosters, n_sims, seed):
    simmod = import_simulator()
    league = load_json(DATA / "league.json", {}) or {}
    users = load_json(DATA / "users.json", []) or []
    players = load_json(DATA / "players.json", {}) or {}
    season = str(league.get("season"))
    projections = load_json(DATA / "simulator" / season / "inputs" / "player_weekly_projections.json", {}) or {}
    raw_schedule = load_json(resolve_schedule_path(season), {}) or {}
    validation = simmod.validate_inputs(league, rosters, users, players, raw_schedule, projections)
    if not validation.get("validation_passed"):
        raise RuntimeError(f"Decision Lab simulator validation failed: {validation}")
    return simmod.run_simulation(
        league=league,
        rosters=rosters,
        users=users,
        players=players,
        raw_schedule=raw_schedule,
        projections=projections,
        n_sims=n_sims,
        seed=seed,
    )


def classify_decision(focus_cmp: Dict[str, Any], team_state: str):
    d = focus_cmp.get("delta") or {}
    s = focus_cmp.get("strategic") or {}
    title = float(d.get("championship_probability") or 0.0)
    playoff = float(d.get("playoff_probability") or 0.0)
    dynasty = float(s.get("market_dynasty_delta") or 0.0)
    break_glass = float(s.get("break_glass_delta") or 0.0)

    contender = team_state in {"contender", "elite_contender"}
    if contender and title <= -0.03:
        band = "reject_competitive_damage"
    elif contender and title < -0.01 and break_glass < 0:
        band = "lean_reject"
    elif title >= 0.01 and dynasty >= 0:
        band = "accept"
    elif playoff >= 0 and dynasty > 0 and break_glass >= 0:
        band = "lean_accept"
    elif not contender and dynasty > 0:
        band = "accept_retool_value"
    else:
        band = "needs_context"
    return {
        "band": band,
        "team_state": team_state,
        "rule_based": True,
        "note": "Decision band is a transparent heuristic overlay; underlying simulator and GM deltas remain primary evidence.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    scenario_path = Path(args.scenario)
    scenario = load_json(scenario_path, {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    actions = scenario.get("actions") or []
    if not focus_uid:
        raise ValueError("scenario.focus_user_id is required")
    if not actions:
        raise ValueError("scenario.actions must contain at least one action")
    if args.sims < 100:
        raise ValueError("--sims must be at least 100")

    canonical_rosters = load_json(DATA / "rosters.json", []) or []
    hypothetical_rosters, pick_transfers = apply_actions(canonical_rosters, actions)

    baseline = run_sims(canonical_rosters, args.sims, args.seed)
    hypothetical = run_sims(hypothetical_rosters, args.sims, args.seed)
    base_by_uid, hyp_by_uid = team_index(baseline), team_index(hypothetical)

    touched = {focus_uid}
    for a in actions:
        for k in ("user_id", "from_user_id", "to_user_id"):
            if a.get(k) is not None:
                touched.add(str(a.get(k)))

    comparisons = {}
    for uid in sorted(touched):
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

    idx = franchise_index()
    team_state = str((idx.get(focus_uid) or {}).get("team_state") or "unknown")
    recommendation = classify_decision(focus_cmp, team_state)

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
        "recommendation": recommendation,
    }

    output = Path(args.output) if args.output else DATA / "decision_lab" / "outputs" / f"{report['scenario_id']}.json"
    write_json(output, report)
    print(json.dumps(report, indent=2))
    print(f"\nWrote Decision Lab report: {output}")


if __name__ == "__main__":
    main()
