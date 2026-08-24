#!/usr/bin/env python3
"""Build non-leaky historical pre-action context for Behavioral Intelligence 3.0.

The builder replays recorded ownership backwards from the current Sleeper
rosters.  For each completed trade, acquisition/drop, and draft selection it
captures the focal manager's roster *immediately before* the action.

Historical player quality is deliberately conservative: only PRIOR completed
season FSFFL production is used.  Players without a prior-season sample (most
rookies, breakouts, etc.) are UNKNOWN rather than treated as replacement level.
Exact historical market values and historical external draft boards are not
available, so this module does not invent BPA/reach labels.

This is an offline/cache builder.  Market Sweep should consume the resulting
compact artifact; it should not replay history in the interactive path.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data")
MODEL_VERSION = "FSFFL-Behavioral-Action-Context-1.0"
POSITIONS = ("QB", "RB", "WR", "TE")
# 12-team SF/0.5PPR contextual roster targets, not player-value rankings.
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


def epoch(v):
    try:
        x = int(v)
        return x if x < 10**12 else x // 1000
    except Exception:
        return 0


def iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def player_index():
    raw = loadj(DATA / "players.json", {})
    if isinstance(raw, list):
        return {str(x.get("player_id")): x for x in raw if x.get("player_id") is not None}
    return {str(k): v for k, v in raw.items()}


def current_ownership():
    owners, roster_ids = {}, {}
    for r in loadj(DATA / "rosters.json", []):
        uid = str(r.get("owner_id") or "")
        if not uid:
            continue
        owners[uid] = set(str(x) for x in (r.get("players") or []) if x is not None)
        roster_ids[str(r.get("roster_id"))] = uid
    return owners, roster_ids


def prior_season_quality(players):
    """Return season -> pid -> {rank, ppg, starter_quality} using only season data."""
    by_season = {}
    for season in range(2022, 2026):
        rows = loadj(DATA / "stats" / "fsffl" / str(season) / "player_season_fsffl.json", [])
        pos_rows = defaultdict(list)
        for r in rows:
            pid = str(r.get("player_id") or "")
            p = players.get(pid, {})
            pos = str(r.get("position") or p.get("position") or "")
            try:
                games = int(r.get("games_with_stats") or 0)
                ppg = float(r.get("fsffl_ppg") or 0)
            except Exception:
                continue
            if pos in POSITIONS and games >= 4:
                pos_rows[pos].append((pid, ppg))
        index = {}
        for pos, vals in pos_rows.items():
            vals.sort(key=lambda z: z[1], reverse=True)
            n = max(1, len(vals))
            cutoff = STARTER_RANK_CUTOFF[pos]
            for rank, (pid, ppg) in enumerate(vals, 1):
                index[pid] = {
                    "position": pos,
                    "rank": rank,
                    "ppg": round(ppg, 3),
                    "position_percentile": round(1 - (rank - 1) / n, 4),
                    "starter_quality": rank <= cutoff,
                }
        by_season[season] = index
    return by_season


def action_quality(pid, action_season, qidx):
    # Never use same-season realized production to interpret an action.
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
        qtarget = QUALITY_TARGET[pos]
        itarget = INVENTORY_TARGET[pos]
        qdef = clamp((qtarget - quality[pos]) / qtarget)
        idef = clamp((itarget - counts[pos]) / itarget)
        # Quality is more informative; inventory prevents a single elite player
        # from making a thin room appear complete.
        need[pos] = round(.68 * qdef + .32 * idef, 4)
        surplus[pos] = round(clamp((quality[pos] - qtarget) / max(1.0, qtarget)), 4)
    relevant = sum(counts.values())
    known_n = sum(known.values())
    coverage = known_n / relevant if relevant else 0.0
    return {
        "roster_size": len(roster),
        "position_counts": counts,
        "prior_season_quality_known": known,
        "prior_season_quality_unknown": unknown,
        "starter_quality_counts": quality,
        "position_need": need,
        "position_surplus": surplus,
        "quality_coverage": round(coverage, 4),
    }


def draft_times():
    """Map draft_id to start timestamp. Pick ordering is exact; timestamps are approximate."""
    out = {}
    for entry in loadj(DATA / "drafts.json", []):
        d = entry.get("draft") or {}
        did = str(d.get("draft_id") or "")
        if did:
            out[did] = epoch(d.get("start_time") or d.get("created"))
    return out


def build_events(roster_ids):
    events = []
    # Completed trades are reversed atomically by transaction.
    for t in loadj(DATA / "trade_ledger.json", []):
        if t.get("status") != "complete":
            continue
        ts = epoch(t.get("created") or t.get("created_epoch_ms"))
        sides = []
        for s in t.get("sides") or []:
            uid = str(s.get("user_id") or "")
            if not uid:
                continue
            sides.append({
                "user_id": uid,
                "manager": s.get("manager"),
                "team_name": s.get("team_name"),
                "received": [str(x.get("player_id")) for x in (s.get("received_players") or []) if x.get("player_id")],
                "sent": [str(x.get("player_id")) for x in (s.get("sent_players") or []) if x.get("player_id")],
                "received_positions": [str(x.get("position") or "") for x in (s.get("received_players") or [])],
                "sent_positions": [str(x.get("position") or "") for x in (s.get("sent_players") or [])],
                "received_pick_count": len(s.get("received_picks") or []),
                "sent_pick_count": len(s.get("sent_picks") or []),
            })
        if sides:
            events.append({"ts": ts, "season": int(t.get("season") or 0), "kind": "trade", "id": str(t.get("transaction_id")), "sides": sides})

    # Waiver/free-agent moves can add and drop in one atomic transaction.
    for r in loadj(DATA / "acquisition_ledger.json", []):
        if r.get("status") != "complete":
            continue
        uid = str(r.get("user_id") or "")
        if not uid:
            uid = roster_ids.get(str(r.get("roster_id") or ""), "")
        if not uid:
            continue
        events.append({
            "ts": epoch(r.get("created") or r.get("created_epoch_ms")),
            "season": int(r.get("season") or 0),
            "kind": "acquisition",
            "id": str(r.get("transaction_id")),
            "sides": [{
                "user_id": uid,
                "manager": r.get("manager"),
                "team_name": r.get("team_name"),
                "received": [str(x.get("player_id")) for x in (r.get("players_added") or []) if x.get("player_id")],
                "sent": [str(x.get("player_id")) for x in (r.get("players_dropped") or []) if x.get("player_id")],
                "received_positions": [str(x.get("position") or "") for x in (r.get("players_added") or [])],
                "sent_positions": [str(x.get("position") or "") for x in (r.get("players_dropped") or [])],
                "faab_bid": r.get("faab_bid"),
                "transaction_type": r.get("type"),
            }],
        })

    # Draft ledger has pick order but not per-pick timestamps. Use draft start plus
    # pick number as a deterministic ordering surrogate; this is explicitly
    # confidence-labeled and is not used to infer market movement during the draft.
    starts = draft_times()
    for r in loadj(DATA / "draft_ledger.json", []):
        if r.get("draft_status") != "complete":
            continue
        uid = str(r.get("user_id") or "")
        if not uid:
            uid = roster_ids.get(str(r.get("roster_id") or ""), "")
        pid = str(r.get("player_id") or "")
        if not uid or not pid:
            continue
        pick_no = int(r.get("pick_no") or 0)
        start = starts.get(str(r.get("draft_id") or ""), 0)
        events.append({
            "ts": start + pick_no,
            "season": int(r.get("season") or 0),
            "kind": "draft",
            "id": f"{r.get('draft_id')}:{pick_no}",
            "draft_id": str(r.get("draft_id") or ""),
            "pick_no": pick_no,
            "round": int(r.get("round") or 0),
            "draft_slot": int(r.get("draft_slot") or 0),
            "sides": [{
                "user_id": uid,
                "manager": r.get("manager"),
                "team_name": r.get("team_name"),
                "received": [pid],
                "sent": [],
                "received_positions": [str(r.get("position") or "")],
                "sent_positions": [],
            }],
        })
    return sorted(events, key=lambda e: (e["ts"], e["id"]), reverse=True)


def avg_for_positions(metric, positions):
    vals = [float(metric[p]) for p in positions if p in metric]
    return round(sum(vals) / len(vals), 4) if vals else None


def build():
    players = player_index()
    ownership, roster_ids = current_ownership()
    qidx = prior_season_quality(players)
    events = build_events(roster_ids)
    records = []
    anomalies = 0
    reverse_ops = 0

    for ev in events:
        # Current ownership here represents state immediately AFTER this action.
        side_after = {}
        for side in ev["sides"]:
            uid = side["user_id"]
            ownership.setdefault(uid, set())
            side_after[uid] = set(ownership[uid])

        # Reverse the action atomically: remove what was received, restore what was sent/dropped.
        local_anom = defaultdict(int)
        for side in ev["sides"]:
            uid = side["user_id"]
            for pid in side.get("received") or []:
                reverse_ops += 1
                if pid not in ownership[uid]:
                    local_anom[uid] += 1
                    anomalies += 1
                ownership[uid].discard(pid)
            for pid in side.get("sent") or []:
                reverse_ops += 1
                if pid in ownership[uid]:
                    local_anom[uid] += 1
                    anomalies += 1
                ownership[uid].add(pid)

        # State now represents immediately BEFORE this event.
        for side in ev["sides"]:
            uid = side["user_id"]
            pre = summarize_roster(ownership[uid], ev["season"], players, qidx)
            rec_pos = [p for p in side.get("received_positions") or [] if p in POSITIONS]
            sent_pos = [p for p in side.get("sent_positions") or [] if p in POSITIONS]
            structural_conf = clamp(1.0 - .16 * local_anom[uid])
            quality_conf = pre["quality_coverage"]
            if ev["season"] <= 2022:
                quality_conf = 0.0
            context_conf = round(.62 * structural_conf + .38 * quality_conf, 4)
            record = {
                "event_id": ev["id"],
                "event_type": ev["kind"],
                "event_time_utc": iso(ev["ts"]),
                "season": ev["season"],
                "user_id": uid,
                "manager": side.get("manager"),
                "team_name": side.get("team_name"),
                "players_acquired": side.get("received") or [],
                "players_sent_or_dropped": side.get("sent") or [],
                "positions_acquired": rec_pos,
                "positions_sent_or_dropped": sent_pos,
                "pre_action": pre,
                "acquired_position_need": avg_for_positions(pre["position_need"], rec_pos),
                "acquired_position_surplus": avg_for_positions(pre["position_surplus"], rec_pos),
                "sent_position_need": avg_for_positions(pre["position_need"], sent_pos),
                "sent_position_surplus": avg_for_positions(pre["position_surplus"], sent_pos),
                "roster_reconstruction_anomalies": local_anom[uid],
                "roster_reconstruction_confidence": round(structural_conf, 4),
                "historical_quality_confidence": round(quality_conf, 4),
                "context_confidence": context_conf,
                "quality_basis": "prior_completed_season_fsffl_only",
                "uses_same_season_future_results": False,
                "exact_historical_market_value_available": False,
            }
            if ev["kind"] == "draft":
                record.update({
                    "draft_id": ev.get("draft_id"),
                    "pick_no": ev.get("pick_no"),
                    "round": ev.get("round"),
                    "draft_slot": ev.get("draft_slot"),
                    "bpa_reach_signal": None,
                    "bpa_reach_signal_reason": "time_appropriate_historical_draft_board_not_available",
                })
            if ev["kind"] == "acquisition":
                record["faab_bid"] = side.get("faab_bid")
                record["transaction_type"] = side.get("transaction_type")
            if ev["kind"] == "trade":
                record["received_pick_count"] = side.get("received_pick_count", 0)
                record["sent_pick_count"] = side.get("sent_pick_count", 0)
            records.append(record)

    event_counts = defaultdict(int)
    owner_counts = defaultdict(int)
    confs = []
    for r in records:
        event_counts[r["event_type"]] += 1
        owner_counts[r["user_id"]] += 1
        confs.append(r["context_confidence"])
    return {
        "model_version": MODEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Offline pre-action context cache for context-normalized manager behavior.",
        "policy": {
            "backward_ownership_replay_from_current_rosters": True,
            "same_season_future_results_allowed": False,
            "historical_quality_uses_prior_completed_season_only": True,
            "unknown_player_quality_is_not_treated_as_bad": True,
            "exact_historical_market_values_available": False,
            "historical_external_draft_board_available": False,
            "interactive_market_sweep_should_rebuild_this_artifact": False,
        },
        "audit": {
            "action_side_count": len(records),
            "event_type_counts": dict(event_counts),
            "owner_count": len(owner_counts),
            "reverse_player_operations": reverse_ops,
            "reverse_replay_anomalies": anomalies,
            "reverse_replay_anomaly_rate": round(anomalies / max(1, reverse_ops), 5),
            "average_context_confidence": round(sum(confs) / max(1, len(confs)), 4),
        },
        "actions": sorted(records, key=lambda r: (r.get("event_time_utc") or "", r["event_id"], r["user_id"])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = build()
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(out), **payload["audit"]}, indent=2))


if __name__ == "__main__":
    main()
