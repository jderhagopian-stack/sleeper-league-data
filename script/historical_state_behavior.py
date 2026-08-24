#!/usr/bin/env python3
"""Historical competitive-state reconstruction for FSFFL owner behavior.

Reconstructs a confidence-weighted competitive state immediately before each
completed trade side using information that would have been available at the
time:
- in-season: standings and points through games completed before the trade;
- offseason/camp: prior-season final standings and points;
- approximate pre-trade roster age as a small window/context adjustment.

The reconstruction intentionally avoids using the eventual outcome of the same
season for an in-season transaction. Pre-trade roster context is approximate,
so every label carries an explicit confidence score and provenance.

This module is a fast runtime read layer. It does not mutate canonical data and
is cached per process.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DATA = Path("data")
TRADE_LEDGER = DATA / "trade_ledger.json"
PRETRADE = DATA / "pretrade_roster_context.json"

# Opening Thursday of each NFL season. Only used to determine which fantasy
# weeks were already complete at a historical transaction timestamp.
SEASON_START_UTC = {
    2022: "2022-09-08T00:00:00+00:00",
    2023: "2023-09-07T00:00:00+00:00",
    2024: "2024-09-05T00:00:00+00:00",
    2025: "2025-09-04T00:00:00+00:00",
    2026: "2026-09-10T00:00:00+00:00",
}
REGULAR_SEASON_WEEKS = 14
STATE_THRESHOLDS = ((.78, "elite_contender"), (.55, "contender"), (.35, "retool"))


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def percentile(value: float, values: Iterable[float]) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return .5
    if len(vals) == 1:
        return .5
    below = sum(v < value for v in vals)
    equal = sum(v == value for v in vals)
    return clamp((below + .5 * equal) / len(vals))


def classify(score: float) -> str:
    for threshold, label in STATE_THRESHOLDS:
        if score >= threshold:
            return label
    return "rebuild"


def parse_dt(value: str) -> datetime:
    v = str(value or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def completed_week_before_trade(season: int, created_utc: str) -> int:
    start_raw = SEASON_START_UTC.get(int(season))
    if not start_raw:
        return 0
    start = parse_dt(start_raw)
    when = parse_dt(created_utc)
    days = (when - start).total_seconds() / 86400.0
    # A fantasy week beginning Thursday is treated complete the following
    # Tuesday (five full days later). A trade before then cannot use that week.
    if days < 5:
        return 0
    completed = int(math.floor((days - 5) / 7.0)) + 1
    return max(0, min(REGULAR_SEASON_WEEKS, completed))


@lru_cache(maxsize=None)
def matchup_data(season: int):
    path = DATA / "stats" / "fsffl" / str(season) / "league_matchups_raw.json"
    return load_json(path, {}) or {}


def standings_through(season: int, through_week: int) -> Dict[str, Dict[str, float]]:
    raw = matchup_data(int(season))
    rows: Dict[str, Dict[str, float]] = defaultdict(lambda: {"wins": 0.0, "losses": 0.0, "ties": 0.0, "pf": 0.0, "games": 0.0})
    for week_s, entries in raw.items():
        try:
            week = int(week_s)
        except (TypeError, ValueError):
            continue
        if week > int(through_week):
            continue
        groups: Dict[str, List[dict]] = defaultdict(list)
        for entry in entries or []:
            rid = str(entry.get("roster_id"))
            rows[rid]["pf"] += sf(entry.get("points"))
            rows[rid]["games"] += 1
            groups[str(entry.get("matchup_id"))].append(entry)
        for pair in groups.values():
            if len(pair) != 2:
                continue
            a, b = pair
            ap, bp = sf(a.get("points")), sf(b.get("points"))
            ar, br = str(a.get("roster_id")), str(b.get("roster_id"))
            if ap > bp:
                rows[ar]["wins"] += 1; rows[br]["losses"] += 1
            elif bp > ap:
                rows[br]["wins"] += 1; rows[ar]["losses"] += 1
            else:
                rows[ar]["ties"] += 1; rows[br]["ties"] += 1
    for r in rows.values():
        g = max(1.0, r["games"])
        r["win_pct"] = (r["wins"] + .5 * r["ties"]) / g
        r["ppg"] = r["pf"] / g
    return dict(rows)


def pretrade_index():
    out = {}
    for row in load_json(PRETRADE, []) or []:
        tx = str(row.get("transaction_id"))
        out[tx] = row
    return out


def age_context(tx_context: dict, user_id: str) -> Tuple[float, float | None]:
    participants = tx_context.get("participants") or []
    ages = [sf(p.get("approx_pretrade_average_age"), None) for p in participants]
    ages = [x for x in ages if x is not None]
    target = None
    for p in participants:
        if str(p.get("user_id")) == str(user_id):
            target = sf(p.get("approx_pretrade_average_age"), None)
            break
    if target is None or len(ages) < 2:
        return .5, target
    # Older-than-league roster gets a small current-window tilt; younger gets a
    # future-window tilt. This never dominates performance evidence.
    pct = percentile(target, ages)
    return pct, target


def performance_signal(season: int, roster_id: str, through_week: int):
    table = standings_through(season, through_week)
    row = table.get(str(roster_id))
    if not row:
        return None
    win_pcts = [r.get("win_pct", .5) for r in table.values()]
    ppgs = [r.get("ppg", 0.0) for r in table.values()]
    wp = percentile(sf(row.get("win_pct"), .5), win_pcts)
    pp = percentile(sf(row.get("ppg"), 0.0), ppgs)
    return {
        "record_percentile": round(wp, 4),
        "points_percentile": round(pp, 4),
        "wins": row.get("wins", 0.0),
        "losses": row.get("losses", 0.0),
        "ties": row.get("ties", 0.0),
        "ppg": round(sf(row.get("ppg")), 2),
    }


def reconstruct_side_state(trade: dict, side: dict, ctx: dict) -> dict:
    season = int(trade.get("season") or 0)
    created_utc = str(trade.get("created_utc") or "")
    uid = str(side.get("user_id") or "")
    rid = str(side.get("roster_id") or "")
    phase = str(ctx.get("phase") or "unknown")
    age_pct, avg_age = age_context(ctx, uid)
    completed_week = completed_week_before_trade(season, created_utc)

    mode = "in_season_pretrade_results"
    source_season = season
    source_week = completed_week
    perf = performance_signal(season, rid, completed_week) if completed_week > 0 else None
    confidence = .0

    if perf:
        sample_rel = clamp(completed_week / 8.0)
        raw = .64 * perf["record_percentile"] + .31 * perf["points_percentile"] + .05 * age_pct
        # Early-season standings are noisy; shrink toward neutral until enough
        # games are played.
        score = .5 + sample_rel * (raw - .5)
        confidence = clamp(.42 + .055 * completed_week, .42, .94)
    else:
        # Before the season starts (or when historical matchup data is absent),
        # anchor to the prior completed season. This avoids using future results
        # from the season in which the trade occurs.
        source_season = season - 1
        source_week = REGULAR_SEASON_WEEKS
        perf = performance_signal(source_season, rid, REGULAR_SEASON_WEEKS) if source_season >= 2022 else None
        mode = "prior_season_anchor_plus_pretrade_roster_context"
        if perf:
            raw = .67 * perf["record_percentile"] + .28 * perf["points_percentile"] + .05 * age_pct
            score = .5 + .78 * (raw - .5)
            confidence = .68
        else:
            # No defensible performance anchor: leave near neutral and mark LOW.
            score = .5 + .12 * (age_pct - .5)
            confidence = .24
            mode = "low_information_pretrade_roster_context_only"

    # Explicitly reduce confidence for approximate roster reconstruction.
    if ctx.get("context_quality"):
        confidence = max(.15, confidence - .04)
    state = classify(score) if confidence >= .35 else "unknown"
    return {
        "transaction_id": str(trade.get("transaction_id")),
        "created_utc": created_utc,
        "season": str(season),
        "phase": phase,
        "user_id": uid,
        "roster_id": rid,
        "manager": side.get("manager"),
        "team_name": side.get("team_name"),
        "historical_state": state,
        "historical_state_score": round(score, 4),
        "historical_state_confidence": round(confidence, 4),
        "reconstruction_mode": mode,
        "performance_source_season": str(source_season) if source_season else None,
        "performance_through_week": int(source_week),
        "performance_evidence": perf,
        "approx_pretrade_average_age": avg_age,
        "pretrade_context_quality": ctx.get("context_quality"),
        "uses_future_same_season_results": False,
    }


def assets_on_side(side: dict):
    received_players = side.get("received_players") or []
    sent_players = side.get("sent_players") or []
    received_picks = side.get("received_picks") or []
    sent_picks = side.get("sent_picks") or []
    return received_players, sent_players, received_picks, sent_picks


def state_profile(rows: List[dict], trade_by_tx: Dict[str, dict]) -> dict:
    pos_in = Counter(); pos_out = Counter(); picks_in = picks_out = 0; multi = 0; faab_in = faab_out = 0
    confidences = []
    for row in rows:
        trade = trade_by_tx.get(row["transaction_id"]) or {}
        side = next((s for s in (trade.get("sides") or []) if str(s.get("user_id")) == row["user_id"]), {})
        rp, sp, rpk, spk = assets_on_side(side)
        pos_in.update(str(p.get("position") or "UNK") for p in rp)
        pos_out.update(str(p.get("position") or "UNK") for p in sp)
        picks_in += len(rpk); picks_out += len(spk)
        faab_in += int(side.get("faab_received") or 0); faab_out += int(side.get("faab_sent") or 0)
        if len(rp) + len(sp) + len(rpk) + len(spk) >= 4:
            multi += 1
        confidences.append(sf(row.get("historical_state_confidence")))
    n = len(rows)
    total_pos = sum(pos_in.values()) or 1
    return {
        "trade_sample": n,
        "average_state_reconstruction_confidence": round(sum(confidences) / max(1, n), 4),
        "positions_acquired": dict(pos_in),
        "positions_sent": dict(pos_out),
        "position_acquisition_share": {k: round(v / total_pos, 4) for k, v in pos_in.items()},
        "picks_acquired": picks_in,
        "picks_sent": picks_out,
        "average_net_picks_acquired_per_trade": round((picks_in - picks_out) / max(1, n), 4),
        "multi_asset_rate": round(multi / max(1, n), 4),
        "faab_net_acquired": faab_in - faab_out,
    }


@lru_cache(maxsize=1)
def build_index() -> dict:
    trades = [t for t in (load_json(TRADE_LEDGER, []) or []) if str(t.get("status")) == "complete"]
    ctx_by_tx = pretrade_index()
    side_rows = []
    trade_by_tx = {str(t.get("transaction_id")): t for t in trades}
    for trade in trades:
        tx = str(trade.get("transaction_id"))
        ctx = ctx_by_tx.get(tx, {})
        for side in trade.get("sides") or []:
            side_rows.append(reconstruct_side_state(trade, side, ctx))

    grouped: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for row in side_rows:
        if row["historical_state"] == "unknown":
            continue
        grouped[(row["user_id"], row["historical_state"])].append(row)

    owner_profiles: Dict[str, dict] = defaultdict(lambda: {"by_state": {}})
    for (uid, state), rows in grouped.items():
        owner_profiles[uid]["by_state"][state] = state_profile(rows, trade_by_tx)
    for uid, payload in owner_profiles.items():
        samples = sum(v.get("trade_sample", 0) for v in payload["by_state"].values())
        payload["state_labeled_trade_sample"] = samples

    labeled = [r for r in side_rows if r["historical_state"] != "unknown"]
    return {
        "model_version": "FSFFL-Historical-Trade-State-1.0",
        "method": "pretrade_only_results_plus_approx_pretrade_roster_context",
        "historical_state_at_trade_reconstruction_enabled": True,
        "historical_state_at_trade_reconstruction_is_approximate": True,
        "future_same_season_result_leakage_allowed": False,
        "trade_side_count": len(side_rows),
        "state_labeled_trade_side_count": len(labeled),
        "coverage": round(len(labeled) / max(1, len(side_rows)), 4),
        "state_counts": dict(Counter(r["historical_state"] for r in side_rows)),
        "sides": side_rows,
        "owner_state_behavior_profiles": dict(owner_profiles),
    }


def owner_state_profile(user_id: str, state: str) -> dict:
    idx = build_index()
    return (((idx.get("owner_state_behavior_profiles") or {}).get(str(user_id)) or {}).get("by_state") or {}).get(str(state)) or {}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/historical_trade_state_context.json")
    args = ap.parse_args()
    payload = build_index()
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("model_version", "trade_side_count", "state_labeled_trade_side_count", "coverage", "state_counts")}, indent=2))


if __name__ == "__main__":
    main()
