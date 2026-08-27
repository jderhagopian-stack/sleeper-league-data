#!/usr/bin/env python3
"""Historical FSFFL trade analysis as a time-travel wrapper around GM 3.0.

Historical Trade Analysis does not own a separate valuation or grading formula.
Its responsibilities are:
1. reconstruct the exact pre-trade league state,
2. load time-frozen GM3 inputs that were knowable at the trade timestamp,
3. delegate the decision evaluation to the canonical GM3/Decision Lab core,
4. report realized outcome afterward as a strictly separate hindsight layer.

If adequate frozen GM3 inputs are unavailable, the trade is NOT GRADED. The
module never substitutes present-day values and never falls back to the retired
v1.0 pick/need/player-quality composite.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from fsffl_historical_state_provider import (
    HistoricalStateProvider,
    completed_transactions,
    roster_to_user,
)
from build_behavioral_action_context import player_index, prior_season_quality, summarize_roster, owner_directory

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCRIPT = ROOT / "script"
MODEL_VERSION = "FSFFL-GM-Historical-Trade-Analysis-1.2"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def find_trade(provider: HistoricalStateProvider, season: str, transaction_id: str) -> Dict[str, Any]:
    for tx in completed_transactions(provider.data(str(season))):
        if str(tx.get("transaction_id") or "") == str(transaction_id):
            if str(tx.get("type") or "") != "trade":
                raise ValueError(f"Transaction {transaction_id} is not a trade")
            return tx
    raise KeyError(f"Trade {season}/{transaction_id} not found")


def asset_names(players: Dict[str, Any], ids):
    out = []
    for pid in ids:
        p = players.get(str(pid)) or {}
        out.append(str(p.get("full_name") or p.get("name") or pid))
    return out


def side_assets(tx: Dict[str, Any], rid: str) -> Dict[str, Any]:
    adds, drops = tx.get("adds") or {}, tx.get("drops") or {}
    received_players = [str(pid) for pid, rr in adds.items() if str(rr) == str(rid)]
    sent_players = [str(pid) for pid, rr in drops.items() if str(rr) == str(rid)]
    received_picks, sent_picks = [], []
    for p in tx.get("draft_picks") or []:
        key = f"pick:{p.get('season')}:R{p.get('round')}:orig{p.get('roster_id')}"
        if str(p.get("owner_id")) == str(rid):
            received_picks.append(key)
        if str(p.get("previous_owner_id")) == str(rid):
            sent_picks.append(key)
    return {
        "received_players": received_players,
        "sent_players": sent_players,
        "received_picks": received_picks,
        "sent_picks": sent_picks,
    }


def transaction_actions(tx: Dict[str, Any], data: Dict[str, Any]):
    """Translate a Sleeper transaction into canonical Decision Lab trade actions."""
    r2u = roster_to_user(data)
    moves = {}

    def add_move(src_rid, dst_rid, player=None, pick=None):
        src, dst = r2u.get(str(src_rid)), r2u.get(str(dst_rid))
        if not src or not dst:
            raise RuntimeError(f"Unable to resolve historical roster transfer {src_rid}->{dst_rid}")
        key = (src, dst)
        row = moves.setdefault(key, {"type": "trade", "from_user_id": src, "to_user_id": dst, "players": [], "picks": []})
        if player is not None:
            row["players"].append(str(player))
        if pick is not None:
            row["picks"].append(str(pick))

    adds, drops = tx.get("adds") or {}, tx.get("drops") or {}
    for pid, dst in adds.items():
        src = drops.get(pid)
        if src is not None and str(src) != str(dst):
            add_move(src, dst, player=pid)

    for p in tx.get("draft_picks") or []:
        prev, new = p.get("previous_owner_id"), p.get("owner_id")
        if prev is None or new is None or str(prev) == str(new):
            continue
        aid = f"pick:{p.get('season')}:R{p.get('round')}:orig{p.get('roster_id')}"
        add_move(prev, new, pick=aid)

    return list(moves.values())


def realized_outcome(transaction_id: str, uid: str) -> Dict[str, Any]:
    rows = loadj(DATA / "transaction_performance_index.json", [])
    hits = [
        r for r in rows
        if str(r.get("transaction_id") or "") == str(transaction_id)
        and str(r.get("acquiring_user_id") or "") == str(uid)
    ]
    return {
        "player_acquisition_rows": hits,
        "acquired_player_fsffl_points_after_trade": round(
            sum(float(r.get("fsffl_points_for_acquirer_after_trade") or 0) for r in hits), 2
        ),
        "tracked_player_count": len(hits),
        "note": "Descriptive hindsight only; never fed into the at-the-time GM3 evaluation.",
    }



def _pick_key(row: Dict[str, Any]) -> str:
    return f"pick:{row.get('season')}:R{row.get('round')}:orig{row.get('original_roster_id', row.get('roster_id'))}"


def _side_asset_keys(side: Dict[str, Any], received=True):
    pkey = "received_players" if received else "sent_players"
    kkey = "received_picks" if received else "sent_picks"
    players = [
        f"player:{x.get('player_id')}" for x in (side.get(pkey) or [])
        if isinstance(x, dict) and x.get("player_id") is not None
    ]
    picks = [_pick_key(x) for x in (side.get(kkey) or []) if isinstance(x, dict)]
    return players + picks


def _asset_label(aid: str, players: Dict[str, Any], conversions: Dict[str, Dict[str, Any]]):
    if aid.startswith("player:"):
        pid=aid.split(":",1)[1]
        p=players.get(pid) or {}
        return str(p.get("full_name") or p.get("name") or pid)
    # Do not annotate a pick with the player ultimately selected unless this
    # franchise actually held and exercised the pick. That conversion is a
    # lineage event, not an inherent property of the pick while it was owned.
    return aid


def _draft_events(conversions):
    drafts=loadj(DATA / "drafts.json", [])
    start_by_season={}
    for row in drafts:
        d=(row or {}).get("draft") or {}
        if d.get("season") is not None and d.get("start_time") is not None:
            start_by_season[str(d.get("season"))]=int(d.get("start_time"))
    out=[]
    for c in conversions:
        ts=start_by_season.get(str(c.get("season")))
        if ts:
            out.append({
                "created":ts,
                "event_type":"draft_selection",
                "pick_asset_key":str(c.get("pick_asset_key")),
                "player_asset_key":str(c.get("player_asset_key")),
                "player_name":c.get("player_name"),
                "drafted_by_user_id":str(c.get("drafted_by_user_id")),
                "pick_no":c.get("pick_no"),
            })
    return out


def build_asset_lineage(root_created: int, uid: str, roster_id: str, root_assets, players):
    """Trace what a franchise actually turned the acquired assets into.

    Downstream trades are followed recursively. When a lineage asset is packaged
    with unrelated assets, every return asset is retained but clearly marked as
    mixed attribution rather than falsely claiming the original trade alone
    produced the entire return.
    """
    trades=loadj(DATA / "trade_ledger.json", [])
    transactions=loadj(DATA / "transactions.json", [])
    conversions=loadj(DATA / "draft_pick_conversion_index.json", [])
    conversion_map={str(x.get("pick_asset_key")):x for x in conversions if x.get("pick_asset_key")}
    timeline=[]

    for tr in trades:
        if int(tr.get("created") or 0)<=int(root_created):
            continue
        side=next((x for x in (tr.get("sides") or []) if str(x.get("user_id"))==str(uid)),None)
        if side:
            timeline.append({
                "created":int(tr.get("created") or 0),
                "event_type":"trade",
                "transaction_id":str(tr.get("transaction_id")),
                "side":side,
            })

    for tx in transactions:
        if int(tx.get("created") or 0)<=int(root_created) or str(tx.get("status"))!="complete":
            continue
        if str(tx.get("type"))=="trade":
            continue
        if str(roster_id) not in {str(x) for x in (tx.get("roster_ids") or [])}:
            continue
        for pid,rid in (tx.get("drops") or {}).items():
            if str(rid)==str(roster_id):
                timeline.append({
                    "created":int(tx.get("created") or 0),
                    "event_type":"release",
                    "transaction_id":str(tx.get("transaction_id")),
                    "player_asset_key":f"player:{pid}",
                    "transaction_type":tx.get("type"),
                })

    timeline.sort(key=lambda x:(int(x.get("created") or 0), x.get("event_type")!="draft_selection"))
    live=set(map(str,root_assets))
    nodes={aid:{"asset_key":aid,"label":_asset_label(aid,players,conversion_map),"root_asset":True} for aid in live}
    events=[]
    mixed=0

    def convert_pick_for_player(player_aid: str, created: int):
        for pick in list(live):
            c=conversion_map.get(pick) or {}
            if (
                str(c.get("player_asset_key"))==str(player_aid)
                and str(c.get("drafted_by_user_id"))==str(uid)
            ):
                live.remove(pick); live.add(str(player_aid))
                nodes.setdefault(str(player_aid),{
                    "asset_key":str(player_aid),
                    "label":c.get("player_name") or _asset_label(str(player_aid),players,conversion_map),
                    "root_asset":False,
                })
                events.append({
                    "created":max(int(created)-1,0),
                    "event_type":"draft_selection",
                    "from_assets":[pick],"to_assets":[str(player_aid)],
                    "description":f"{pick} became {c.get('player_name')} at pick {c.get('pick_no')}.",
                    "attribution":"direct",
                })
                return True
        return False

    for ev in timeline:
        typ=ev.get("event_type")
        if typ=="trade":
            side=ev["side"]
            sent=_side_asset_keys(side,received=False)
            # Historical draft timestamps are not always retained. If a player
            # selected with a live lineage pick is now being traded, convert the
            # pick immediately before this transaction.
            for a in list(sent):
                if a.startswith("player:") and a not in live:
                    convert_pick_for_player(a,int(ev.get("created") or 0))
            hit=[a for a in sent if a in live]
            if not hit:
                continue
            received=_side_asset_keys(side,received=True)
            attribution="direct" if all(a in live for a in sent) else "mixed_with_non_lineage_assets"
            if attribution!="direct": mixed+=1
            for a in hit: live.discard(a)
            for a in received:
                live.add(a)
                nodes.setdefault(a,{"asset_key":a,"label":_asset_label(a,players,conversion_map),"root_asset":False})
            events.append({
                "created":ev["created"],"event_type":"downstream_trade",
                "transaction_id":ev.get("transaction_id"),
                "from_assets":hit,"to_assets":received,"attribution":attribution,
                "description":(
                    f"Traded {', '.join(_asset_label(a,players,conversion_map) for a in hit)} for "
                    f"{', '.join(_asset_label(a,players,conversion_map) for a in received) or 'no tracked player/pick return'}."
                ),
            })
        elif typ=="release":
            aid=str(ev.get("player_asset_key"))
            if aid not in live:
                convert_pick_for_player(aid,int(ev.get("created") or 0))
            if aid in live:
                live.remove(aid)
                events.append({
                    "created":ev["created"],"event_type":"released",
                    "transaction_id":ev.get("transaction_id"),
                    "from_assets":[aid],"to_assets":[],"attribution":"direct",
                    "description":f"{_asset_label(aid,players,conversion_map)} was released via {ev.get('transaction_type')}.",
                })

    # Finally convert any still-held pick whose draft is complete and which
    # this franchise actually exercised. This captures old drafts even when
    # drafts.json no longer carries the historical start timestamp.
    for pick in list(live):
        c=conversion_map.get(pick) or {}
        if c and str(c.get("drafted_by_user_id"))==str(uid):
            player=str(c.get("player_asset_key"))
            live.remove(pick); live.add(player)
            nodes.setdefault(player,{
                "asset_key":player,
                "label":c.get("player_name") or _asset_label(player,players,conversion_map),
                "root_asset":False,
            })
            events.append({
                "created":0,
                "event_type":"draft_selection",
                "from_assets":[pick],"to_assets":[player],
                "description":f"{pick} became {c.get('player_name')} at pick {c.get('pick_no')}.",
                "attribution":"direct",
            })

    current_assets=loadj(DATA / "fsffl_asset_values.json", {}) or {}
    current_map={}
    for p in current_assets.get("players") or []:
        current_map[f"player:{p.get('player_id')}"]=float(p.get("intrinsic_dynasty") or p.get("fsffl_value") or 0)
    for p in current_assets.get("picks") or []:
        if p.get("asset_id"):
            current_map[str(p.get("asset_id"))]=float(p.get("intrinsic_dynasty") or p.get("fsffl_value") or 0)

    return {
        "root_assets":[{"asset_key":a,"label":_asset_label(a,players,conversion_map)} for a in root_assets],
        "events":events,
        "terminal_assets":[
            {"asset_key":a,"label":_asset_label(a,players,conversion_map),"current_intrinsic_value":round(current_map.get(a,0.0),1)}
            for a in sorted(live)
        ],
        "terminal_current_intrinsic_value":round(sum(current_map.get(a,0.0) for a in live),1),
        "mixed_attribution_events":mixed,
        "methodology_note":"Downstream asset lineage is factual. Mixed-package returns are retained but explicitly marked mixed; exact economic attribution is not claimed.",
    }


def keep_assets_reference(transaction_id: str, sent_assets: Dict[str, Any], players):
    """Observed reference for assets surrendered, not a fictional alternate-history replay."""
    perf=loadj(DATA / "transaction_performance_index.json", [])
    conv={str(x.get("pick_asset_key")):x for x in loadj(DATA / "draft_pick_conversion_index.json", []) if x.get("pick_asset_key")}
    rows=[]
    for pid in sent_assets.get("sent_players") or []:
        hit=next((r for r in perf if str(r.get("transaction_id"))==str(transaction_id) and str(r.get("player_id"))==str(pid)),None)
        p=players.get(str(pid)) or {}
        rows.append({
            "asset_key":f"player:{pid}",
            "label":str(p.get("full_name") or p.get("name") or pid),
            "observed_post_trade_points":round(float((hit or {}).get("fsffl_points_for_acquirer_after_trade") or 0),2),
            "reference_type":"observed_player_output_after_trade",
        })
    for aid in sent_assets.get("sent_picks") or []:
        c=conv.get(str(aid))
        rows.append({
            "asset_key":str(aid),
            "label":str(aid),
            "drafted_player":(c or {}).get("player_name"),
            "pick_no":(c or {}).get("pick_no"),
            "reference_type":"actual_slot_conversion" if c else "unresolved_or_future_pick",
        })
    return {
        "assets":rows,
        "note":"This is a keep-the-original-assets reference, not a claim about exact alternate history. Later manager choices, trades, waivers and lineup decisions would have changed if the original trade never happened.",
    }


def default_bundle_path(season: str, transaction_id: str) -> Path:
    return DATA / "historical_gm3" / str(season) / f"{transaction_id}.json"


def analyze(season: str, transaction_id: str, sims=1000, seed=20260821, bundle_path: str | None = None):
    requested = Path(bundle_path) if bundle_path else default_bundle_path(str(season), str(transaction_id))
    bundle = loadj(requested, None) if requested.exists() else None

    # Fast path: the bundle builder already paid the cost to reconstruct the
    # exact historical state. Reuse that immutable snapshot instead of fetching
    # the entire Sleeper league chain a second time.
    snap = (bundle or {}).get("historical_state_snapshot") or {}
    bundled_tx = (bundle or {}).get("historical_transaction")
    bundled_rosters = (bundle or {}).get("historical_rosters")
    if snap and bundled_tx and bundled_rosters:
        tx = bundled_tx
        state = SimpleNamespace(
            roster_players={str(k): set(map(str, v or [])) for k, v in (snap.get("roster_players") or {}).items()},
            roster_taxi={str(k): set(map(str, v or [])) for k, v in (snap.get("roster_taxi") or {}).items()},
            roster_reserve={str(k): set(map(str, v or [])) for k, v in (snap.get("roster_reserve") or {}).items()},
            pick_owners={str(k): str(v) for k, v in (snap.get("pick_owners") or {}).items()},
            faab_used={str(k): v for k, v in (snap.get("faab_used") or {}).items()},
            reconstruction=snap.get("reconstruction") or {},
        )
        data = {
            "league": (bundle or {}).get("league") or {},
            "users": (bundle or {}).get("users") or [],
            "rosters": bundled_rosters,
        }
        state_source_mode = "frozen_bundle_snapshot"
    else:
        provider = HistoricalStateProvider()
        tx = find_trade(provider, str(season), str(transaction_id))
        state = provider.pre_transaction_state(str(season), str(transaction_id))
        data = provider.data(str(season))
        state_source_mode = "provider_reload"

    players = player_index()
    qidx = prior_season_quality(players)
    r2u = roster_to_user(data)
    owners = owner_directory(data)
    actions = transaction_actions(tx, data)

    touched = {str(x) for x in (tx.get("roster_ids") or [])}
    touched |= {str(x) for x in (tx.get("adds") or {}).values() if x is not None}
    touched |= {str(x) for x in (tx.get("drops") or {}).values() if x is not None}
    participant_uids = sorted({r2u[rid] for rid in touched if rid in r2u})

    root_trade_ledger = next(
        (
            x for x in loadj(DATA / "trade_ledger.json", [])
            if str(x.get("transaction_id") or "") == str(transaction_id)
        ),
        {},
    )
    root_ledger_side_by_uid = {
        str(x.get("user_id")): x for x in (root_trade_ledger.get("sides") or [])
        if x.get("user_id") is not None
    }

    sides = {}
    for rid in sorted(touched):
        uid = r2u.get(rid)
        if not uid:
            continue
        assets = side_assets(tx, rid)
        summary = summarize_roster(state.roster_players.get(rid, set()), int(season), players, qidx)
        historical_side = root_ledger_side_by_uid.get(str(uid)) or {}
        sides[uid] = {
            "user_id": uid,
            "roster_id": rid,
            "manager": historical_side.get("manager") or (owners.get(uid) or {}).get("manager"),
            "team_name": historical_side.get("team_name") or (owners.get(uid) or {}).get("team_name"),
            "pretrade_roster": {
                "player_ids": sorted(state.roster_players.get(rid, set())),
                "player_names": asset_names(players, sorted(state.roster_players.get(rid, set()))),
                "summary": summary,
                "faab_used": state.faab_used.get(rid),
                "owned_future_picks": sorted(k for k, owner in state.pick_owners.items() if str(owner) == rid),
            },
            "trade_assets": {
                **assets,
                "received_player_names": asset_names(players, assets["received_players"]),
                "sent_player_names": asset_names(players, assets["sent_players"]),
            },
            "realized_outcome": realized_outcome(str(transaction_id), uid),
        }
        root_assets=[
            *(f"player:{x}" for x in assets["received_players"]),
            *assets["received_picks"],
        ]
        sides[uid]["hindsight"]={
            "asset_lineage":build_asset_lineage(
                int(tx.get("created") or 0),uid,rid,root_assets,players
            ),
            "keep_assets_reference":keep_assets_reference(str(transaction_id),assets,players),
        }

    adapter = load_module(SCRIPT / "historical_trade_gm3_adapter.py", "historical_trade_gm3_adapter")
    gm3 = adapter.evaluate(
        state, data, actions, participant_uids, bundle, sims=int(sims), seed=int(seed)
    )

    for uid, side in sides.items():
        side["gm3_decision_at_time"] = (gm3.get("team_results") or {}).get(uid) if gm3.get("status") == "GRADED_BY_GM3_CORE" else {
            "status": "NOT_GRADED",
            "reason": gm3.get("reason"),
        }

    return {
        "model_version": MODEL_VERSION,
        "season": int(season),
        "transaction_id": str(transaction_id),
        "trade_time_utc": iso_ms(int(tx.get("created") or 0)),
        "historical_state_provider": {
            "model_version": "FSFFL-Historical-State-Provider-1.0",
            "source": (state.reconstruction or {}).get("source"),
            "reconstruction_confidence": (state.reconstruction or {}).get("confidence"),
            "future_draftees_removed": (state.reconstruction or {}).get("future_draftees_removed"),
            "analysis_state_source_mode": state_source_mode,
        },
        "actions": actions,
        "participant_user_ids": participant_uids,
        "gm3_evaluation": gm3,
        "time_frozen_bundle_path": str(requested.relative_to(ROOT)) if requested.is_absolute() and str(requested).startswith(str(ROOT)) else str(requested),
        "sides": sides,
        "policy": {
            "historical_module_is_time_travel_wrapper_not_scoring_model": True,
            "same_gm3_core_as_current_trade_analysis": True,
            "decision_quality_uses_only_information_available_before_trade": True,
            "same_season_future_results_leakage_forbidden": True,
            "outcome_layer_separate_from_decision_layer": True,
            "hindsight_traces_draft_conversions_and_downstream_trades": True,
            "mixed_lineage_attribution_is_flagged_not_overclaimed": True,
            "keep_assets_reference_is_descriptive_not_causal_counterfactual": True,
            "current_player_values_not_backfilled_into_historical_grade": True,
            "standalone_v1_process_score_retired": True,
            "missing_historical_inputs_result_in_not_graded": True,
            "alternate_history_state_provider_reused": True,
        },
        "limitations": [
            "Historical reconstruction alone is not sufficient to grade a trade.",
            "A grade is produced only when the trade date has a complete time-frozen GM3 input bundle.",
            "Until those inputs exist, the report preserves the reconstructed facts and realized outcome but explicitly returns NOT GRADED.",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    ap.add_argument("--transaction-id", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260821)
    ap.add_argument("--gm3-bundle", default=None)
    args = ap.parse_args()
    result = analyze(args.season, args.transaction_id, args.sims, args.seed, args.gm3_bundle)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "model_version": result["model_version"],
        "transaction_id": result["transaction_id"],
        "trade_time_utc": result["trade_time_utc"],
        "gm3_status": result["gm3_evaluation"]["status"],
        "reason": result["gm3_evaluation"].get("reason"),
    }, indent=2))


if __name__ == "__main__":
    main()
