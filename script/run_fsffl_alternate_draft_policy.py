#!/usr/bin/env python3
"""FSFFL Alternate History 0.6b: probabilistic historical rookie-draft policy.

This stage converts 0.6a availability windows into a coupled redraft distribution.
It deliberately avoids future NFL outcomes. Historical-safe signals only:
- actual same-draft selection order as contemporaneous market evidence;
- the manager's actual selection in that round as revealed preference;
- manager position tendencies from PRIOR completed rookie drafts;
- previous-season final roster composition as a pre-draft need proxy.

Reference-path limitation:
- actual pick-trade topology is frozen for 0.6b. If 0.5 historical trade
  branching later changes pick ownership, the draft is rerun for that branch.

No current GM 3.0 numeric values or current player ranks are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
from run_fsffl_alternate_draft_candidates import raw_draft, run as run_windows, user_to_roster_for_season
from run_fsffl_downstream_dependencies import load
from run_fsffl_postseason_consequences_v3 import run as run_postseason

DATA = Path("data")
DEFAULT_SIMS = 5000


def player_positions() -> Dict[str, str]:
    raw = load(DATA / "players.json")
    return {str(pid): str(row.get("position") or "") for pid, row in raw.items()}


def seed_for(scenario_id: str, draft_season: str) -> int:
    raw = f"alternate-history-draft-policy|{scenario_id}|{draft_season}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def pick_name(pick: Dict[str, Any]) -> str:
    meta = pick.get("metadata") or {}
    first = str(meta.get("first_name") or "").strip()
    last = str(meta.get("last_name") or "").strip()
    return " ".join(x for x in (first, last) if x).strip() or str(pick.get("player_id") or "unknown")


def normalized_picks(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for raw in entry.get("picks") or []:
        meta = raw.get("metadata") or {}
        out.append({
            "pick_no": int(raw.get("pick_no") or 0),
            "round": int(raw.get("round") or 0),
            "draft_slot": int(raw.get("draft_slot") or 0),
            "player_id": str(raw.get("player_id") or meta.get("player_id") or ""),
            "player_name": pick_name(raw),
            "position": str(meta.get("position") or ""),
            "picked_by_user_id": str(raw.get("picked_by") or ""),
        })
    return sorted(out, key=lambda x: x["pick_no"])


def raw_history() -> Dict[str, Any]:
    return load(DATA / "alternate_history" / "source_history" / "sleeper_history.json")


def prior_draft_tendencies(target_season: int) -> Dict[str, Counter]:
    """Manager position choices from drafts strictly before target season."""
    out: Dict[str, Counter] = defaultdict(Counter)
    cache = raw_history()
    for season_data in cache.get("history") or []:
        season = int((season_data.get("league") or {}).get("season") or 0)
        if season <= 0 or season >= target_season:
            continue
        for entry in season_data.get("drafts") or []:
            for p in entry.get("picks") or []:
                uid = str(p.get("picked_by") or "")
                pos = str((p.get("metadata") or {}).get("position") or "")
                if uid and pos:
                    out[uid][pos] += 1
                    out[uid]["__TOTAL__"] += 1
    return out


def previous_season_roster_counts(draft_season: int, positions: Dict[str, str]) -> Dict[str, Counter]:
    """Use prior season's frozen Sleeper rosters as a no-hindsight need proxy."""
    target = str(draft_season - 1)
    cache = raw_history()
    out: Dict[str, Counter] = defaultdict(Counter)
    for season_data in cache.get("history") or []:
        if str((season_data.get("league") or {}).get("season")) != target:
            continue
        for roster in season_data.get("rosters") or []:
            uid = str(roster.get("owner_id") or "")
            if not uid:
                continue
            for pid in roster.get("players") or []:
                pos = positions.get(str(pid), "")
                if pos:
                    out[uid][pos] += 1
        break
    return out


def league_position_medians(counts: Dict[str, Counter]) -> Dict[str, float]:
    positions = {p for c in counts.values() for p in c if not p.startswith("__")}
    result: Dict[str, float] = {}
    for pos in positions:
        vals = sorted(float(c.get(pos, 0)) for c in counts.values())
        if not vals:
            continue
        n = len(vals)
        result[pos] = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    return result


def softmax_choice(rng: random.Random, scored: List[Tuple[float, Dict[str, Any]]]) -> Dict[str, Any]:
    if not scored:
        raise ah.AlternateHistoryError("Draft policy received an empty candidate set")
    mx = max(x[0] for x in scored)
    weights = [math.exp(max(-20.0, min(20.0, score - mx))) for score, _ in scored]
    total = sum(weights)
    needle = rng.random() * total
    running = 0.0
    for weight, (_, player) in zip(weights, scored):
        running += weight
        if needle <= running:
            return player
    return scored[-1][1]


def candidate_score(
    player: Dict[str, Any],
    current_pick_no: int,
    controller_uid: str,
    actual_controller_selection: str,
    tendencies: Dict[str, Counter],
    roster_counts: Dict[str, Counter],
    medians: Dict[str, float],
) -> float:
    """Historical-safe latent preference score; scale is intentionally compact."""
    pos = str(player.get("position") or "")
    actual_pick_no = int(player.get("pick_no") or 0)

    # Contemporaneous market board. Being selected one slot earlier matters,
    # but not enough to erase a manager's revealed preference.
    market = -0.42 * abs(actual_pick_no - current_pick_no)

    # Revealed preference: the manager actually chose this player in this round
    # when available at their real slot. This is direct evidence, not hindsight.
    revealed = 1.55 if str(player.get("player_id")) == str(actual_controller_selection) else 0.0

    hist = tendencies.get(controller_uid) or Counter()
    total = float(hist.get("__TOTAL__", 0))
    tendency_share = float(hist.get(pos, 0)) / total if total > 0 and pos else 0.0
    tendency = 0.65 * tendency_share

    owner_counts = roster_counts.get(controller_uid) or Counter()
    median = float(medians.get(pos, 0.0))
    deficit = max(-2.0, min(3.0, median - float(owner_counts.get(pos, 0)))) if pos else 0.0
    need = 0.28 * deficit

    return market + revealed + tendency + need


def run(scenario_path: Path, n_sims: int = DEFAULT_SIMS) -> Path:
    windows = load(run_windows(scenario_path))
    post = load(run_postseason(scenario_path))
    draft_season = int(windows["draft_season"])
    entry = raw_draft(str(draft_season))
    draft = entry.get("draft") or {}
    teams = int((draft.get("settings") or {}).get("teams") or 12)
    rounds = int((draft.get("settings") or {}).get("rounds") or 0)
    if teams <= 0 or rounds <= 0:
        raise ah.AlternateHistoryError("Historical draft settings unavailable")

    picks = normalized_picks(entry)
    by_round_slot = {(p["round"], p["draft_slot"]): p for p in picks}
    positions = player_positions()
    tendencies = prior_draft_tendencies(draft_season)
    roster_counts = previous_season_roster_counts(draft_season, positions)
    medians = league_position_medians(roster_counts)
    user_to_roster = user_to_roster_for_season(str(draft_season))

    # Actual slot belongs to an original owner via draft_order. A traded pick's
    # controller is `picked_by`; that controller follows the original slot into
    # its alternate position in this reference topology.
    actual_slot_by_roster: Dict[str, int] = {}
    for uid, slot in (draft.get("draft_order") or {}).items():
        rid = user_to_roster.get(str(uid))
        if rid:
            actual_slot_by_roster[rid] = int(slot)

    alt_finish = post.get("alternate", {}).get("playoffs", {}).get("finish_by_roster") or {}
    alt_slot_by_roster = dict(actual_slot_by_roster)
    for rid, finish in alt_finish.items():
        alt_slot_by_roster[str(rid)] = 13 - int(finish)

    # Map each alternate slot to the corresponding actual source slot.
    source_slot_for_alt: Dict[int, int] = {}
    for rid, actual_slot in actual_slot_by_roster.items():
        source_slot_for_alt[int(alt_slot_by_roster.get(rid, actual_slot))] = int(actual_slot)
    if len(source_slot_for_alt) != teams:
        raise ah.AlternateHistoryError("Alternate draft-slot mapping is not one-to-one")

    scenario_id = str(windows.get("scenario_id") or "scenario")
    rng = random.Random(seed_for(scenario_id, str(draft_season)))
    selection_counts: Dict[Tuple[int, int, str], int] = defaultdict(int)
    roster_round_counts: Dict[Tuple[str, int, str], int] = defaultdict(int)
    controller_round_counts: Dict[Tuple[str, int, str], int] = defaultdict(int)

    # Market pool is the actual draft board. Every player can be selected once.
    for _ in range(int(n_sims)):
        drafted: set[str] = set()
        sim_roster_counts = {uid: Counter(c) for uid, c in roster_counts.items()}

        for rnd in range(1, rounds + 1):
            for alt_slot in range(1, teams + 1):
                current_pick_no = (rnd - 1) * teams + alt_slot
                source_slot = source_slot_for_alt[alt_slot]
                actual_source_pick = by_round_slot.get((rnd, source_slot))
                if actual_source_pick is None:
                    continue
                controller_uid = str(actual_source_pick.get("picked_by_user_id") or "")
                controller_rid = user_to_roster.get(controller_uid) or ""
                actual_selection = str(actual_source_pick.get("player_id") or "")

                available = [p for p in picks if p["round"] == rnd and p["player_id"] not in drafted]
                if not available:
                    continue

                # Keep candidate search local to contemporaneous board position.
                # This prevents unrealistic hindsight reaches while still allowing
                # a moved owner to choose players just after their new slot.
                local = [
                    p for p in available
                    if abs(int(p["pick_no"]) - current_pick_no) <= 4
                ]
                if actual_selection and actual_selection not in drafted:
                    actual_player = next((p for p in available if p["player_id"] == actual_selection), None)
                    if actual_player is not None and actual_player not in local:
                        local.append(actual_player)
                if not local:
                    local = available[: min(5, len(available))]

                scored = [
                    (
                        candidate_score(
                            p,
                            current_pick_no,
                            controller_uid,
                            actual_selection,
                            tendencies,
                            sim_roster_counts,
                            medians,
                        ),
                        p,
                    )
                    for p in local
                ]
                chosen = softmax_choice(rng, scored)
                drafted.add(chosen["player_id"])
                if controller_uid and chosen.get("position"):
                    sim_roster_counts.setdefault(controller_uid, Counter())[chosen["position"]] += 1

                selection_counts[(rnd, alt_slot, chosen["player_id"])] += 1
                if controller_rid:
                    roster_round_counts[(controller_rid, rnd, chosen["player_id"])] += 1
                if controller_uid:
                    controller_round_counts[(controller_uid, rnd, chosen["player_id"])] += 1

    player_by_id = {p["player_id"]: p for p in picks}

    slot_distributions = []
    for rnd in range(1, rounds + 1):
        for slot in range(1, teams + 1):
            rows = []
            for (r, s, pid), count in selection_counts.items():
                if r == rnd and s == slot:
                    p = player_by_id.get(pid) or {"player_id": pid, "player_name": pid, "position": ""}
                    rows.append({
                        "player_id": pid,
                        "player_name": p.get("player_name"),
                        "position": p.get("position"),
                        "probability": round(count / float(n_sims), 4),
                    })
            rows.sort(key=lambda x: x["probability"], reverse=True)
            source_slot = source_slot_for_alt[slot]
            source_pick = by_round_slot.get((rnd, source_slot)) or {}
            slot_distributions.append({
                "round": rnd,
                "alternate_slot": slot,
                "source_actual_slot": source_slot,
                "controller_user_id": source_pick.get("picked_by_user_id"),
                "top_candidates": rows[:8],
            })

    focus_rid = str(post.get("focus_roster_id"))
    focus = []
    for rnd in range(1, rounds + 1):
        rows = []
        for (rid, r, pid), count in roster_round_counts.items():
            if rid == focus_rid and r == rnd:
                p = player_by_id.get(pid) or {"player_id": pid, "player_name": pid, "position": ""}
                rows.append({
                    "player_id": pid,
                    "player_name": p.get("player_name"),
                    "position": p.get("position"),
                    "probability": round(count / float(n_sims), 4),
                })
        rows.sort(key=lambda x: x["probability"], reverse=True)
        focus.append({"round": rnd, "top_candidates": rows[:10]})

    report = {
        "model_version": "Fantasy-Alternate-History-0.6b-draft-policy",
        "scenario_id": scenario_id,
        "draft_season": str(draft_season),
        "n_sims": int(n_sims),
        "rng_seed": seed_for(scenario_id, str(draft_season)),
        "design_invariants": {
            "future_nfl_outcomes_used": False,
            "current_gm3_numeric_values_used": False,
            "actual_draft_order_used_as_contemporaneous_market_evidence": True,
            "prior_manager_draft_tendencies_only": True,
            "previous_season_roster_used_as_need_proxy": True,
            "reference_pick_trade_topology_frozen": True,
        },
        "policy_weights": {
            "market_pick_distance_per_slot": -0.42,
            "revealed_actual_selection_bonus": 1.55,
            "prior_position_tendency_max": 0.65,
            "roster_need_per_player_deficit": 0.28,
            "candidate_market_radius_picks": 4,
        },
        "focus_roster_id": focus_rid,
        "focus_selection_distributions": focus,
        "slot_selection_distributions": slot_distributions,
        "confidence": {
            "market_availability": "HIGH",
            "revealed_manager_preference": "HIGH",
            "position_need_proxy": "MEDIUM",
            "overall": "MEDIUM_HIGH",
            "note": "Rerun required for branches where prior pick trades do not survive 0.5 historical policy resolution.",
        },
    }
    out = ah.write_isolated_json(f"results/{scenario_id}/draft_policy_0_6b.json", report)
    print(out)
    print(json.dumps({
        "n_sims": report["n_sims"],
        "focus_roster_id": focus_rid,
        "focus_selection_distributions": focus,
    }, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.6b historical draft policy")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    args = parser.parse_args()
    run(args.scenario, n_sims=max(250, int(args.sims)))


if __name__ == "__main__":
    main()
