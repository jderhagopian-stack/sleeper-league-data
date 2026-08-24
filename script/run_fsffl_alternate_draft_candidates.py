#!/usr/bin/env python3
"""Alternate History 0.6a: historical rookie-draft consequence windows.

This stage does NOT choose alternate players. It uses raw Sleeper draft history
and a separately backvalidated playoff-slot rule to determine:
- which playoff owners move to different rookie draft slots;
- which actual selections are no longer guaranteed to be available;
- which players become newly available opportunities when an owner moves up.

The actual draft itself is contemporaneous information, so using its revealed
selection order as a market/availability boundary does not introduce future NFL
performance hindsight.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import alternate_history_engine as ah
from infer_fsffl_historical_draft_order import run as run_inference
from run_fsffl_downstream_dependencies import load
from run_fsffl_postseason_consequences_v3 import run as run_postseason

DATA = Path("data")


def active_season() -> str:
    league = load(DATA / "league.json") or {}
    season = str(league.get("season") or "")
    if not season:
        raise ah.AlternateHistoryError("Active Sleeper season unavailable")
    return season


def raw_draft(season: str) -> Dict[str, Any]:
    """Return an authoritative draft snapshot without leaking future history.

    Completed seasons come only from the immutable Alternate History archive.
    The active season may fall back to the canonical live Sleeper snapshot,
    because by definition that draft has already occurred in the present-day
    league state and is required to reach the Simulator 1.0 boundary.
    """
    season = str(season)
    cache = load(DATA / "alternate_history" / "source_history" / "sleeper_history.json")
    for season_data in cache.get("history") or []:
        if str((season_data.get("league") or {}).get("season")) != season:
            continue
        for entry in season_data.get("drafts") or []:
            draft = entry.get("draft") or {}
            if str(draft.get("season")) == season:
                return entry

    if season == active_season():
        current = load(DATA / "drafts.json") or []
        for entry in current if isinstance(current, list) else []:
            draft = entry.get("draft") or {}
            if str(draft.get("season")) == season:
                return entry

    raise ah.AlternateHistoryError(f"Raw Sleeper draft not found for {season}")


def user_to_roster_for_season(season: str) -> Dict[str, str]:
    season = str(season)
    cache = load(DATA / "alternate_history" / "source_history" / "sleeper_history.json")
    for season_data in cache.get("history") or []:
        if str((season_data.get("league") or {}).get("season")) != season:
            continue
        out = {}
        for roster in season_data.get("rosters") or []:
            if roster.get("owner_id") is not None and roster.get("roster_id") is not None:
                out[str(roster["owner_id"])] = str(roster["roster_id"])
        return out

    if season == active_season():
        out = {}
        for roster in load(DATA / "rosters.json") or []:
            if roster.get("owner_id") is not None and roster.get("roster_id") is not None:
                out[str(roster["owner_id"])] = str(roster["roster_id"])
        return out

    return {}


def pick_label(pick: Dict[str, Any]) -> Dict[str, Any]:
    meta = pick.get("metadata") or {}
    name = " ".join(
        x for x in [str(meta.get("first_name") or "").strip(), str(meta.get("last_name") or "").strip()] if x
    ).strip()
    return {
        "pick_no": int(pick.get("pick_no") or 0),
        "round": int(pick.get("round") or 0),
        "draft_slot": int(pick.get("draft_slot") or 0),
        "player_id": str(pick.get("player_id") or meta.get("player_id") or ""),
        "player_name": name or str(pick.get("player_id") or "unknown"),
        "position": meta.get("position"),
        "picked_by_user_id": str(pick.get("picked_by") or ""),
        "roster_id": str(pick.get("roster_id") or ""),
    }


def run(scenario_path: Path) -> Path:
    post = load(run_postseason(scenario_path))
    inference = load(run_inference(scenario_path))
    if not inference.get("component_validation", {}).get("playoff_component", {}).get("validated"):
        raise ah.AlternateHistoryError("Cannot build exact playoff draft windows until playoff draft-slot component backvalidates")

    draft_season = str(int(post["season"]) + 1)
    entry = raw_draft(draft_season)
    draft = entry.get("draft") or {}
    picks = [pick_label(x) for x in (entry.get("picks") or [])]
    rounds = int((draft.get("settings") or {}).get("rounds") or 0)
    teams = int((draft.get("settings") or {}).get("teams") or 12)
    if not rounds or not teams:
        raise ah.AlternateHistoryError("Historical draft settings missing rounds/teams")

    user_to_roster = user_to_roster_for_season(draft_season)
    actual_order_by_roster: Dict[str, int] = {}
    for uid, slot in (draft.get("draft_order") or {}).items():
        rid = user_to_roster.get(str(uid))
        if rid:
            actual_order_by_roster[rid] = int(slot)

    alt_finish = post.get("alternate", {}).get("playoffs", {}).get("finish_by_roster") or {}
    alt_playoff_slots = {str(rid): 13 - int(finish) for rid, finish in alt_finish.items()}

    # Index historical picks by round/slot. Linear dynasty draft has one pick at
    # each slot per round; use the actual selection order as the contemporaneous
    # market window, not as a deterministic alternate choice.
    by_round_slot: Dict[tuple[int, int], Dict[str, Any]] = {}
    by_roster_round: Dict[tuple[str, int], Dict[str, Any]] = {}
    for p in picks:
        by_round_slot[(p["round"], p["draft_slot"])] = p
        rid = user_to_roster.get(p["picked_by_user_id"]) or p.get("roster_id")
        if rid:
            by_roster_round[(str(rid), p["round"])] = p

    affected_owners: List[Dict[str, Any]] = []
    decision_windows: List[Dict[str, Any]] = []
    for rid, alt_slot in sorted(alt_playoff_slots.items(), key=lambda kv: kv[1]):
        actual_slot = actual_order_by_roster.get(rid)
        if actual_slot is None or int(actual_slot) == int(alt_slot):
            continue
        movement = int(actual_slot) - int(alt_slot)  # positive = moved earlier
        affected_owners.append(
            {
                "roster_id": rid,
                "actual_slot": int(actual_slot),
                "alternate_slot": int(alt_slot),
                "slots_earlier": movement,
            }
        )
        for rnd in range(1, rounds + 1):
            actual_pick = by_roster_round.get((rid, rnd))
            lo = min(int(actual_slot), int(alt_slot))
            hi = max(int(actual_slot), int(alt_slot))
            market_window = [
                by_round_slot[(rnd, slot)]
                for slot in range(lo, hi + 1)
                if (rnd, slot) in by_round_slot
            ]
            newly_available = []
            newly_at_risk = []
            if int(alt_slot) < int(actual_slot):
                newly_available = [
                    p for p in market_window if p["draft_slot"] < int(actual_slot)
                ]
            else:
                newly_at_risk = [
                    p for p in market_window if p["draft_slot"] <= int(alt_slot)
                ]
            decision_windows.append(
                {
                    "roster_id": rid,
                    "round": rnd,
                    "actual_slot": int(actual_slot),
                    "alternate_slot": int(alt_slot),
                    "actual_selection": actual_pick,
                    "contemporaneous_market_window": market_window,
                    "newly_available_if_moving_up": newly_available,
                    "actual_target_at_risk_if_moving_down": newly_at_risk,
                    "selection_status": "UNRESOLVED_REQUIRES_HISTORICAL_DRAFT_POLICY",
                }
            )

    focus = str(post.get("focus_roster_id"))
    focus_windows = [x for x in decision_windows if x["roster_id"] == focus]
    report = {
        "model_version": "Fantasy-Alternate-History-0.6a-draft-windows",
        "scenario_id": post.get("scenario_id"),
        "draft_season": draft_season,
        "draft_id": str(draft.get("draft_id") or ""),
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "actual_historical_draft_order_used_only_as_contemporaneous_market_evidence": True,
            "no_alternate_player_selection_assumed_in_0_6a": True,
            "only_backvalidated_playoff_slot_component_used": True,
            "active_season_draft_may_use_canonical_live_snapshot": True,
        },
        "summary": {
            "affected_playoff_owners": len(affected_owners),
            "affected_pick_decisions": len(decision_windows),
            "focus_actual_slot": actual_order_by_roster.get(focus),
            "focus_alternate_slot": alt_playoff_slots.get(focus),
            "focus_rounds_affected": len(focus_windows),
        },
        "affected_playoff_owners_detail": affected_owners,
        "decision_windows": decision_windows,
        "focus_decision_windows": focus_windows,
        "next_stage": (
            "0.6b assigns probabilities to candidate selections using draft-time-safe team need, owner tendencies and contemporaneous market information; "
            "then downstream ownership/transaction dependencies are replayed for each surviving draft branch."
        ),
    }
    return ah.write_isolated_json(
        f"results/{post.get('scenario_id')}/draft_windows_0_6a.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps({
        "summary": report["summary"],
        "focus_decision_windows": report["focus_decision_windows"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
