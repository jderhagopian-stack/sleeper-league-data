#!/usr/bin/env python3
"""Build non-leaky pre-action context for Behavioral Intelligence 3.0.

BI3 does NOT own historical roster reconstruction. It consumes the shared
FSFFLHistoricalStateProvider, extracted from the validated Alternate History
historical-state pattern, and adds only BI-specific roster-quality/need features.

Historical quality is conservative: only PRIOR completed-season FSFFL production
is used. Unknown players remain unknown. Exact historical market values and
historical external draft boards are not invented.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fsffl_historical_state_provider import (
    HistoricalStateProvider,
    completed_transactions,
    provider_audit,
    roster_to_user,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MODEL_VERSION = "FSFFL-Behavioral-Action-Context-1.1"
POSITIONS = ("QB", "RB", "WR", "TE")
QUALITY_TARGET = {"QB": 2.0, "RB": 3.0, "WR": 4.0, "TE": 1.5}
INVENTORY_TARGET = {"QB": 3.0, "RB": 5.0, "WR": 6.0, "TE": 2.5}
STARTER_RANK_CUTOFF = {"QB": 24, "RB": 36, "WR": 48, "TE": 18}


def loadj(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def iso_ms(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def player_index():
    raw = loadj(DATA / "players.json", {})
    if isinstance(raw, list):
        return {str(x.get("player_id")): x for x in raw if x.get("player_id") is not None}
    return {str(k): v for k, v in raw.items()}


def prior_season_quality(players):
    by_season = {}
    for season in range(2022, 2026):
        rows = loadj(DATA / "stats" / "fsffl" / str(season) / "player_season_fsffl.json", [])
        pos_rows = defaultdict(list)
        for r in rows:
            pid = str(r.get("player_id") or "")
            pos = str(r.get("position") or (players.get(pid) or {}).get("position") or "")
            try:
                games = int(r.get("games_with_stats") or 0)
                ppg = float(r.get("fsffl_ppg") or 0)
            except Exception:
                continue
            if pos in POSITIONS and games >= 4:
                pos_rows[pos].append((pid, ppg))
        idx = {}
        for pos, vals in pos_rows.items():
            vals.sort(key=lambda z: z[1], reverse=True)
            n = max(1, len(vals))
            for rank, (pid, ppg) in enumerate(vals, 1):
                idx[pid] = {
                    "position": pos,
                    "rank": rank,
                    "ppg": round(ppg, 3),
                    "position_percentile": round(1 - (rank - 1) / n, 4),
                    "starter_quality": rank <= STARTER_RANK_CUTOFF[pos],
                }
        by_season[season] = idx
    return by_season


def action_quality(pid, action_season, qidx):
    return (qidx.get(int(action_season) - 1) or {}).get(str(pid))


def summarize_roster(roster, action_season, players, qidx):
    counts = {p: 0 for p in POSITIONS}
    known = {p: 0 for p in POSITIONS}
    quality = {p: 0 for p in POSITIONS}
    unknown = {p: 0 for p in POSITIONS}
    for pid in roster:
        pos = str((players.get(str(pid)) or {}).get("position") or "")
        if pos not in POSITIONS:
            continue
        counts[pos] += 1
        q = action_quality(pid, action_season, qidx)
        if q is None:
            unknown[pos] += 1
        else:
            known[pos] += 1
            quality[pos] += int(bool(q.get("starter_quality")))
    need, surplus = {}, {}
    for pos in POSITIONS:
        qdef = clamp((QUALITY_TARGET[pos] - quality[pos]) / QUALITY_TARGET[pos])
        idef = clamp((INVENTORY_TARGET[pos] - counts[pos]) / INVENTORY_TARGET[pos])
        need[pos] = round(.68 * qdef + .32 * idef, 4)
        surplus[pos] = round(clamp((quality[pos] - QUALITY_TARGET[pos]) / max(1.0, QUALITY_TARGET[pos])), 4)
    relevant, known_n = sum(counts.values()), sum(known.values())
    return {
        "roster_size": len(roster),
        "position_counts": counts,
        "prior_season_quality_known": known,
        "prior_season_quality_unknown": unknown,
        "starter_quality_counts": quality,
        "position_need": need,
        "position_surplus": surplus,
        "quality_coverage": round(known_n / relevant, 4) if relevant else 0.0,
    }


def avg_metric(metric, positions):
    vals = [float(metric[p]) for p in positions if p in metric]
    return round(sum(vals) / len(vals), 4) if vals else None


def owner_directory(data):
    out = {}
    for u in data.get("users") or []:
        uid = str(u.get("user_id") or "")
        if not uid:
            continue
        md = u.get("metadata") or {}
        out[uid] = {
            "manager": u.get("display_name") or u.get("username") or uid,
            "team_name": md.get("team_name") or u.get("display_name") or uid,
        }
    return out


def structural_confidence(state):
    anomalies = int((state.reconstruction or {}).get("ownership_anomalies") or 0)
    return clamp(1.0 - .08 * anomalies)


def make_record(*, event_id, event_type, event_ms, season, uid, rid, state, acquired, exited,
                players, qidx, owner, extras=None):
    roster = state.roster_players.get(str(rid), set())
    pre = summarize_roster(roster, season, players, qidx)
    acquired_pos = [str((players.get(p) or {}).get("position") or "") for p in acquired]
    acquired_pos = [p for p in acquired_pos if p in POSITIONS]
    exited_pos = [str((players.get(p) or {}).get("position") or "") for p in exited]
    exited_pos = [p for p in exited_pos if p in POSITIONS]
    sconf = structural_confidence(state)
    qconf = 0.0 if int(season) <= 2022 else pre["quality_coverage"]
    cconf = round(.68 * sconf + .32 * qconf, 4)
    row = {
        "event_id": str(event_id),
        "event_type": event_type,
        "event_time_utc": iso_ms(event_ms),
        "season": int(season),
        "user_id": str(uid),
        "roster_id": str(rid),
        "manager": owner.get("manager"),
        "team_name": owner.get("team_name"),
        "players_acquired": list(acquired),
        "players_sent_or_dropped": list(exited),
        "positions_acquired": acquired_pos,
        "positions_sent_or_dropped": exited_pos,
        "pre_action": pre,
        "acquired_position_need": avg_metric(pre["position_need"], acquired_pos),
        "acquired_position_surplus": avg_metric(pre["position_surplus"], acquired_pos),
        "sent_position_need": avg_metric(pre["position_need"], exited_pos),
        "sent_position_surplus": avg_metric(pre["position_surplus"], exited_pos),
        "roster_reconstruction_confidence": round(sconf, 4),
        "historical_quality_confidence": round(qconf, 4),
        "context_confidence": cconf,
        "historical_state_source": (state.reconstruction or {}).get("source"),
        "quality_basis": "prior_completed_season_fsffl_only",
        "uses_same_season_future_results": False,
        "exact_historical_market_value_available": False,
    }
    if extras:
        row.update(extras)
    return row


def transaction_records(provider, season, players, qidx):
    data = provider.data(str(season))
    r2u = roster_to_user(data)
    owners = owner_directory(data)
    rows = []
    for tx in completed_transactions(data):
        typ = str(tx.get("type") or "")
        if typ not in {"trade", "waiver", "free_agent"}:
            continue
        tid = str(tx.get("transaction_id") or "")
        if not tid:
            continue
        state = provider.pre_transaction_state(str(season), tid)
        adds, drops = tx.get("adds") or {}, tx.get("drops") or {}
        touched = {str(x) for x in (tx.get("roster_ids") or [])}
        touched |= {str(x) for x in adds.values() if x is not None}
        touched |= {str(x) for x in drops.values() if x is not None}
        for rid in sorted(touched):
            uid = r2u.get(rid)
            if not uid:
                continue
            acquired = [str(pid) for pid, rr in adds.items() if str(rr) == rid]
            exited = [str(pid) for pid, rr in drops.items() if str(rr) == rid]
            if not acquired and not exited:
                continue
            extras = {}
            if typ == "trade":
                extras.update({
                    "received_pick_count": sum(1 for p in (tx.get("draft_picks") or []) if str(p.get("owner_id")) == rid),
                    "sent_pick_count": sum(1 for p in (tx.get("draft_picks") or []) if str(p.get("previous_owner_id")) == rid),
                })
                event_type = "trade"
            else:
                extras.update({
                    "faab_bid": (tx.get("settings") or {}).get("waiver_bid") or 0,
                    "transaction_type": typ,
                })
                event_type = "acquisition"
            rows.append(make_record(
                event_id=tid, event_type=event_type, event_ms=int(tx.get("created") or 0),
                season=int(season), uid=uid, rid=rid, state=state,
                acquired=acquired, exited=exited, players=players, qidx=qidx,
                owner=owners.get(uid, {}), extras=extras,
            ))
    return rows


def draft_records(provider, season, players, qidx):
    data = provider.data(str(season))
    owners = owner_directory(data)
    rows = []
    for entry in data.get("drafts") or []:
        draft = entry.get("draft") or {}
        draft_id = str(draft.get("draft_id") or "")
        if not draft_id or str(draft.get("status") or "") != "complete":
            continue
        dtype = str(draft.get("type") or "").lower()
        rounds = int((draft.get("settings") or {}).get("rounds") or 0)
        if dtype and dtype != "linear":
            continue
        if rounds and rounds > 5:
            continue
        start = int(draft.get("start_time") or draft.get("created") or 0)
        for item in provider.draft_pre_pick_states(str(season), draft_id):
            pick, state = item["pick"], item["pre_state"]
            uid, rid = item.get("user_id"), item.get("roster_id")
            pid = pick.get("player_id") or (pick.get("metadata") or {}).get("player_id")
            if not uid or not rid or pid is None:
                continue
            pick_no = int(pick.get("pick_no") or 0)
            rows.append(make_record(
                event_id=f"{draft_id}:{pick_no}", event_type="draft",
                event_ms=start + pick_no, season=int(season), uid=uid, rid=rid,
                state=state, acquired=[str(pid)], exited=[], players=players, qidx=qidx,
                owner=owners.get(uid, {}), extras={
                    "draft_id": draft_id,
                    "pick_no": pick_no,
                    "round": int(pick.get("round") or 0),
                    "draft_slot": int(pick.get("draft_slot") or 0),
                    "bpa_reach_signal": None,
                    "bpa_reach_signal_reason": "time_appropriate_historical_draft_board_not_available",
                    "draft_roster_context_sequence": "exact_pick_order_from_shared_historical_state_provider",
                },
            ))
    return rows


def build():
    players = player_index()
    qidx = prior_season_quality(players)
    provider = HistoricalStateProvider()
    records = []
    for season in provider.seasons():
        records.extend(transaction_records(provider, season, players, qidx))
        if int(season) >= 2023:
            records.extend(draft_records(provider, season, players, qidx))

    event_counts, owner_counts = defaultdict(int), defaultdict(int)
    confs = []
    for r in records:
        event_counts[r["event_type"]] += 1
        owner_counts[r["user_id"]] += 1
        confs.append(float(r["context_confidence"]))
    paudit = provider_audit(provider)
    return {
        "model_version": MODEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Offline BI3 decision context derived from shared historical-state infrastructure.",
        "policy": {
            "shared_historical_state_provider": True,
            "alternate_history_reconstruction_pattern_reused": True,
            "independent_bi3_ownership_replay": False,
            "same_season_future_results_allowed": False,
            "historical_quality_uses_prior_completed_season_only": True,
            "unknown_player_quality_is_not_treated_as_bad": True,
            "exact_historical_market_values_available": False,
            "historical_external_draft_board_available": False,
            "interactive_market_sweep_should_rebuild_this_artifact": False,
        },
        "historical_state_provider": paudit,
        "audit": {
            "action_side_count": len(records),
            "event_type_counts": dict(event_counts),
            "owner_count": len(owner_counts),
            "average_context_confidence": round(sum(confs) / max(1, len(confs)), 4),
            "historical_state_cached_pre_transaction_count": paudit["cached_pre_transaction_state_count"],
            "historical_state_max_cumulative_ownership_anomalies": paudit["max_cumulative_ownership_anomalies"],
        },
        "actions": sorted(records, key=lambda r: (r.get("event_time_utc") or "", r["event_id"], r["user_id"])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    payload = build()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(out), **payload["audit"], "provider": payload["historical_state_provider"]}, indent=2))


if __name__ == "__main__":
    main()
