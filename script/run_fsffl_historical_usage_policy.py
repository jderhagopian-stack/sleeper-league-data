#!/usr/bin/env python3
"""FSFFL Alternate History 0.5c: historical-safe waiver/free-agent usage policy.

Resolves the HISTORICAL_USAGE_POLICY queue produced by 0.5b without using
future NFL outcomes or current GM 3.0 values. Each decision is evaluated from:
- the reconstructed roster immediately before the recorded transaction;
- the counterfactual fork applied to that pre-event state;
- player positions;
- fantasy scoring from weeks strictly BEFORE the transaction week;
- the fact that the historical manager actually chose to make the acquisition.

This is a local/reference policy layer. Multi-event branch replay in 0.7 will
rerun the same policy against accumulated alternate states.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_historical_policy_triage import run as run_triage
from run_fsffl_downstream_dependencies import load

DATA = Path("data")
TRAILING_WEEKS = 3
RECENCY_WEIGHTS = [0.15, 0.30, 0.55]


def positions_index() -> Dict[str, str]:
    raw = load(DATA / "players.json")
    return {str(pid): str(row.get("position") or "").upper() for pid, row in raw.items()}


def _record_points(record: Dict[str, Any]) -> Optional[Tuple[str, int, float]]:
    pid = record.get("player_id") or record.get("player") or record.get("id")
    week = record.get("week") or record.get("leg")
    value = None
    for key in ("points", "fantasy_points", "fsffl_points", "score", "pts"):
        if record.get(key) is not None:
            value = record.get(key)
            break
    if pid is None or week is None or value is None:
        return None
    try:
        return str(pid), int(week), float(value)
    except (TypeError, ValueError):
        return None


def _parse_weekly_payload(payload: Any) -> Dict[int, Dict[str, float]]:
    """Tolerant parser for historical weekly-player artifacts."""
    out: Dict[int, Dict[str, float]] = defaultdict(dict)

    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                rec = _record_points(row)
                if rec:
                    pid, week, pts = rec
                    out[week][pid] = pts
        return dict(out)

    if not isinstance(payload, dict):
        return {}

    # Shape A: {"1": {"9493": 12.3, ...}, "2": ...}
    week_keys = []
    for key in payload:
        try:
            wk = int(key)
            if 1 <= wk <= 25:
                week_keys.append((key, wk))
        except (TypeError, ValueError):
            pass
    if week_keys and len(week_keys) >= max(1, len(payload) // 2):
        for key, week in week_keys:
            node = payload.get(key)
            if isinstance(node, dict):
                for pid, value in node.items():
                    if isinstance(value, (int, float)):
                        out[week][str(pid)] = float(value)
                    elif isinstance(value, dict):
                        rec = _record_points({**value, "player_id": value.get("player_id") or pid, "week": value.get("week") or week})
                        if rec:
                            p, w, pts = rec
                            out[w][p] = pts
            elif isinstance(node, list):
                for row in node:
                    if isinstance(row, dict):
                        rec = _record_points({**row, "week": row.get("week") or week})
                        if rec:
                            p, w, pts = rec
                            out[w][p] = pts
        if out:
            return dict(out)

    # Shape B: {"9493": {"1": 12.3, "2": 8.1}, ...}
    for pid, node in payload.items():
        if isinstance(node, dict):
            for wk, value in node.items():
                try:
                    week = int(wk)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, (int, float)):
                    out[week][str(pid)] = float(value)
                elif isinstance(value, dict):
                    rec = _record_points({**value, "player_id": value.get("player_id") or pid, "week": value.get("week") or week})
                    if rec:
                        p, w, pts = rec
                        out[w][p] = pts
        elif isinstance(node, list):
            for row in node:
                if isinstance(row, dict):
                    rec = _record_points({**row, "player_id": row.get("player_id") or pid})
                    if rec:
                        p, w, pts = rec
                        out[w][p] = pts

    return dict(out)


def matchup_points(season: str) -> Dict[int, Dict[str, float]]:
    path = DATA / "stats" / "fsffl" / season / "league_matchups_raw.json"
    payload = load(path)
    out: Dict[int, Dict[str, float]] = defaultdict(dict)
    for week_key, rows in payload.items():
        week = int(week_key)
        for row in rows or []:
            for pid, pts in (row.get("players_points") or {}).items():
                try:
                    out[week][str(pid)] = float(pts or 0.0)
                except (TypeError, ValueError):
                    pass
    return dict(out)


class HistoricalPoints:
    def __init__(self) -> None:
        self.cache: Dict[str, Dict[int, Dict[str, float]]] = {}
        self.sources: Dict[str, Dict[str, Any]] = {}

    def season(self, season: str) -> Dict[int, Dict[str, float]]:
        season = str(season)
        if season in self.cache:
            return self.cache[season]
        merged = matchup_points(season)
        source = {"matchup_points": True, "player_weekly_fsffl": False}
        weekly_path = DATA / "stats" / "fsffl" / season / "player_weekly_fsffl.json"
        if weekly_path.exists():
            try:
                parsed = _parse_weekly_payload(load(weekly_path))
                for week, rows in parsed.items():
                    merged.setdefault(int(week), {}).update(rows)
                source["player_weekly_fsffl"] = bool(parsed)
                source["player_weekly_records"] = sum(len(x) for x in parsed.values())
            except Exception as exc:  # artifact-shape tolerance is intentional
                source["player_weekly_error"] = f"{type(exc).__name__}: {exc}"
        self.cache[season] = merged
        self.sources[season] = source
        return merged

    def trailing(self, season: str, week: Optional[int], pid: str) -> Dict[str, Any]:
        if week is None or int(week) <= 1:
            return {"score": None, "observations": 0, "weeks": [], "reason": "no_completed_prior_week_window"}
        weekly = self.season(str(season))
        vals: List[Tuple[int, float]] = []
        for w in range(max(1, int(week) - TRAILING_WEEKS), int(week)):
            if str(pid) in weekly.get(w, {}):
                vals.append((w, float(weekly[w][str(pid)])))
        if not vals:
            return {"score": None, "observations": 0, "weeks": [], "reason": "player_not_observed_in_prior_week_sources"}
        weights = RECENCY_WEIGHTS[-len(vals):]
        denom = sum(weights)
        score = sum(v * wt for (_, v), wt in zip(vals, weights)) / denom
        return {
            "score": round(score, 4),
            "observations": len(vals),
            "weeks": [{"week": w, "points": round(v, 2)} for w, v in vals],
        }


def softmax(logits: Dict[str, float]) -> Dict[str, float]:
    finite = {k: v for k, v in logits.items() if math.isfinite(v)}
    if not finite:
        return {k: 0.0 for k in logits}
    mx = max(finite.values())
    exps = {k: math.exp(max(-20.0, min(20.0, v - mx))) for k, v in finite.items()}
    denom = sum(exps.values()) or 1.0
    return {k: round((exps.get(k, 0.0) / denom), 4) for k in logits}


def owner_of(state: ah.LeagueState, pid: str) -> Optional[str]:
    return state.player_owner.get(str(pid))


def roster_players(state: ah.LeagueState, rid: str) -> List[str]:
    rid = str(rid)
    return sorted(pid for pid, owner in state.player_owner.items() if str(owner) == rid)


def event_season_week(event: ah.HistoricalEvent) -> Tuple[Optional[str], Optional[int]]:
    meta = event.metadata or {}
    season = meta.get("source_season") or meta.get("season")
    week = meta.get("leg") or meta.get("week")
    try:
        parsed_week = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed_week = None
    return (str(season) if season is not None else None, parsed_week)


def score_or_zero(signal: Dict[str, Any]) -> float:
    return float(signal.get("score") or 0.0)


def evaluate_event(
    adapter: FSFFLHistoricalAdapter,
    scenario: ah.Scenario,
    event: ah.HistoricalEvent,
    positions: Dict[str, str],
    points: HistoricalPoints,
) -> Dict[str, Any]:
    season, week = event_season_week(event)
    actual_pre = ah.reconstruct_state(adapter, event.timestamp_ms)
    alt_pre = ah.apply_fork(actual_pre, scenario)

    adds_by_roster: Dict[str, List[str]] = defaultdict(list)
    drops_by_roster: Dict[str, List[str]] = defaultdict(list)
    for pid, rid in event.adds.items():
        adds_by_roster[str(rid)].append(str(pid))
    for pid, rid in event.drops.items():
        drops_by_roster[str(rid)].append(str(pid))

    decisions = []
    target_rosters = sorted(set(adds_by_roster) | set(drops_by_roster))
    for rid in target_rosters:
        added = adds_by_roster.get(rid, [])
        dropped = drops_by_roster.get(rid, [])
        roster = roster_players(alt_pre, rid)

        added_rows = []
        all_add_available = True
        for pid in added:
            alt_owner = owner_of(alt_pre, pid)
            available = alt_owner is None or str(alt_owner) == rid
            all_add_available = all_add_available and available
            sig = points.trailing(season, week, pid) if season else {"score": None, "observations": 0, "reason": "missing_season"}
            added_rows.append({
                "player_id": pid,
                "position": positions.get(pid, ""),
                "alternate_pre_owner": alt_owner,
                "available_to_recorded_roster": available,
                "trailing_signal": sig,
            })

        drop_rows = []
        actual_drop_still_owned = True
        for pid in dropped:
            owned = owner_of(alt_pre, pid) == rid
            actual_drop_still_owned = actual_drop_still_owned and owned
            sig = points.trailing(season, week, pid) if season else {"score": None, "observations": 0, "reason": "missing_season"}
            drop_rows.append({
                "player_id": pid,
                "position": positions.get(pid, ""),
                "still_owned_in_alternate_pre_state": owned,
                "trailing_signal": sig,
            })

        add_positions = {positions.get(pid, "") for pid in added if positions.get(pid, "")}
        incumbent_rows = []
        for pid in roster:
            pos = positions.get(pid, "")
            if add_positions and pos not in add_positions:
                continue
            sig = points.trailing(season, week, pid) if season else {"score": None, "observations": 0, "reason": "missing_season"}
            incumbent_rows.append({"player_id": pid, "position": pos, "trailing_signal": sig})
        incumbent_rows.sort(key=lambda x: (score_or_zero(x["trailing_signal"]), x["player_id"]))

        alternate_drop = None
        if incumbent_rows:
            excluded = set(added)
            for row in incumbent_rows:
                if row["player_id"] not in excluded:
                    alternate_drop = row
                    break

        add_scores = [score_or_zero(x["trailing_signal"]) for x in added_rows if x["trailing_signal"].get("score") is not None]
        add_obs = sum(int(x["trailing_signal"].get("observations") or 0) for x in added_rows)
        weakest_score = score_or_zero(alternate_drop["trailing_signal"]) if alternate_drop and alternate_drop["trailing_signal"].get("score") is not None else None
        improvement = None
        if add_scores and weakest_score is not None:
            improvement = (sum(add_scores) / len(add_scores)) - weakest_score

        # Historical action is a strong revealed-action prior. Counterfactual
        # roster quality can shift the acquisition toward a changed drop or no action.
        preserve_acquisition_logit = 1.65
        if improvement is not None:
            preserve_acquisition_logit += max(-1.4, min(1.4, improvement / 8.0))
        elif add_obs == 0:
            preserve_acquisition_logit -= 0.35

        if not all_add_available:
            probs = {"preserve_exact": 0.0, "preserve_add_change_drop": 0.0, "no_action": 1.0}
            reason = "recorded_add_not_available_in_counterfactual_pre_state"
        else:
            exact_logit = preserve_acquisition_logit + (0.8 if actual_drop_still_owned else -math.inf)
            change_logit = preserve_acquisition_logit - 0.35 + (1.1 if not actual_drop_still_owned else -0.5)
            no_action_logit = 0.0
            probs = softmax({
                "preserve_exact": exact_logit,
                "preserve_add_change_drop": change_logit,
                "no_action": no_action_logit,
            })
            reason = "historical_revealed_action_prior_adjusted_by_preweek_usage_and_alt_roster"

        obs = add_obs + sum(int(x["trailing_signal"].get("observations") or 0) for x in incumbent_rows[:3])
        if week is None or season is None:
            confidence = "LOW"
        elif obs >= 6:
            confidence = "MEDIUM_HIGH"
        elif obs >= 2:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        decisions.append({
            "roster_id": rid,
            "season": season,
            "week": week,
            "added": added_rows,
            "actual_dropped": drop_rows,
            "alternate_same_position_incumbents": incumbent_rows[:8],
            "suggested_alternate_drop": alternate_drop,
            "add_vs_weakest_incumbent_trailing_delta": round(improvement, 4) if improvement is not None else None,
            "probabilities": probs,
            "confidence": confidence,
            "reason": reason,
        })

    return {
        "transaction_id": event.transaction_id,
        "timestamp_ms": event.timestamp_ms,
        "event_type": event.event_type,
        "source": (event.metadata or {}).get("source"),
        "decisions": decisions,
    }


def run(scenario_path: Path) -> Path:
    adapter = FSFFLHistoricalAdapter()
    payload = load(scenario_path)
    scenario = ah.scenario_from_json(adapter, payload)
    triage = load(run_triage(scenario_path))
    event_by_id = {str(e.transaction_id): e for e in adapter.completed_events()}
    positions = positions_index()
    points = HistoricalPoints()

    queue = [
        row for row in (triage.get("decision_queue") or [])
        if row.get("classification") == "HISTORICAL_USAGE_POLICY"
    ]
    results = []
    missing = []
    for row in queue:
        tid = str(row.get("transaction_id"))
        event = event_by_id.get(tid)
        if event is None:
            missing.append(tid)
            continue
        results.append(evaluate_event(adapter, scenario, event, positions, points))

    flattened = [d for row in results for d in row.get("decisions") or []]
    expected = {
        "preserve_exact": round(sum(float(d["probabilities"].get("preserve_exact") or 0.0) for d in flattened), 3),
        "preserve_add_change_drop": round(sum(float(d["probabilities"].get("preserve_add_change_drop") or 0.0) for d in flattened), 3),
        "no_action": round(sum(float(d["probabilities"].get("no_action") or 0.0) for d in flattened), 3),
    }
    conf = defaultdict(int)
    for d in flattened:
        conf[str(d.get("confidence"))] += 1

    report = {
        "model_version": "Fantasy-Alternate-History-0.5c-historical-usage",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "future_nfl_outcomes_used": False,
            "current_week_realized_points_used": False,
            "current_gm3_numeric_values_used": False,
            "completed_prior_week_scoring_only": True,
            "historical_completed_transaction_is_revealed_action_prior": True,
            "local_reference_state_only": True,
        },
        "policy_parameters": {
            "trailing_weeks": TRAILING_WEEKS,
            "recency_weights": RECENCY_WEIGHTS,
            "historical_action_base_logit": 1.65,
            "usage_delta_scale_points": 8.0,
            "note": "Heuristic is transparent but not claimed as empirically calibrated. 0.7 reruns against accumulated branch state.",
        },
        "queued_usage_events": len(queue),
        "evaluated_transactions": len(results),
        "evaluated_roster_decisions": len(flattened),
        "missing_transaction_ids": missing,
        "expected_decision_counts": expected,
        "confidence_counts": dict(conf),
        "historical_points_sources": points.sources,
        "decisions": results,
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/historical_usage_policy_0_5c.json", report
    )
    print(out)
    print(json.dumps({
        "queued_usage_events": len(queue),
        "evaluated_roster_decisions": len(flattened),
        "expected_decision_counts": expected,
        "confidence_counts": dict(conf),
        "missing_transaction_ids": missing,
    }, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.5c historical usage policy")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
