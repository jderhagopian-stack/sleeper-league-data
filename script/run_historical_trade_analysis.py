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
    c=conversions.get(aid) or {}
    if aid.startswith("pick:"):
        parts=aid.split(":")
        season=parts[1] if len(parts)>1 else "?"
        rnd=(parts[2].replace("R","") if len(parts)>2 else "?")
        if c.get("draft_slot") is not None:
            pick=f"{season} {rnd}.{int(c.get('draft_slot')):02d}"
        else:
            pick=f"{season} Round {rnd}"
        owner=c.get("original_team_name") or c.get("original_owner_display")
        if owner:
            pick+=f" ({owner} pick)"
        if c.get("player_name"):
            pick+=f" - {c.get('player_name')}"
        return pick
    return aid


def _current_intrinsic_map():
    payload=loadj(DATA / "fsffl_asset_values.json", {}) or {}
    out={}
    for p in payload.get("players") or []:
        out[f"player:{p.get('player_id')}"]=float(p.get("intrinsic_dynasty") or p.get("fsffl_value") or 0)
    for p in payload.get("picks") or []:
        if p.get("asset_id"):
            out[str(p.get("asset_id"))]=float(p.get("intrinsic_dynasty") or p.get("fsffl_value") or 0)
    return out


def _lineage_production(uid: str, roster_id: str, root_transaction_id: str, root_created: int, root_assets, events, conversions):
    """Actual FSFFL production captured from players in the lineage.

    Trade-acquired players use transaction_performance_index, which starts at
    the actual acquisition timestamp. Drafted descendants use while-rostered
    season scoring because the draft occurs before their rookie season.
    """
    perf=loadj(DATA / "transaction_performance_index.json", [])
    roster_rows=loadj(DATA / "record_book" / "franchise_rostered_scoring.json", [])
    root_season=datetime.fromtimestamp(int(root_created)/1000, tz=timezone.utc).year
    source={}
    for aid in root_assets:
        if str(aid).startswith("player:"):
            source[str(aid)]={"kind":"trade","transaction_id":str(root_transaction_id),"season":root_season}
    for ev in events:
        for aid in ev.get("to_assets") or []:
            if not str(aid).startswith("player:"):
                continue
            if ev.get("event_type")=="downstream_trade":
                source.setdefault(str(aid),{"kind":"trade","transaction_id":str(ev.get("transaction_id") or ""),"season":None})
            elif ev.get("event_type")=="draft_selection":
                source.setdefault(str(aid),{"kind":"draft","transaction_id":None,"season":int(ev.get("season") or root_season)})

    player_rows=[]; total=0.0; started=0.0
    for aid,src in source.items():
        pid=aid.split(":",1)[1]
        if src["kind"]=="trade":
            hit=next((
                r for r in perf
                if str(r.get("transaction_id") or "")==str(src.get("transaction_id") or "")
                and str(r.get("acquiring_user_id") or "")==str(uid)
                and str(r.get("player_id") or "")==str(pid)
            ),None)
            pts=float((hit or {}).get("fsffl_points_for_acquirer_after_trade") or 0)
            spts=pts
            seasons=[int((hit or {}).get("season") or root_season)] if hit else []
            name=(hit or {}).get("player_name") or pid
            method="transaction_exact_after_acquisition"
        else:
            start_season=int(src.get("season") or root_season)
            hits=[
                r for r in roster_rows
                if str(r.get("roster_id"))==str(roster_id)
                and str(r.get("player_id"))==str(pid)
                and int(r.get("season") or 0)>=start_season
            ]
            pts=sum(float(r.get("fsffl_points_while_rostered") or 0) for r in hits)
            spts=sum(float(r.get("fsffl_points_while_started") or 0) for r in hits)
            seasons=sorted({int(r.get("season")) for r in hits})
            name=(hits[0].get("player_name") if hits else pid)
            method="drafted_descendant_while_rostered"
        if pts or seasons:
            player_rows.append({
                "asset_key":aid,
                "player_id":pid,
                "player_name":name,
                "fsffl_points_while_rostered":round(pts,2),
                "fsffl_points_while_started":round(spts,2),
                "seasons_counted":seasons,
                "production_method":method,
            })
            total+=pts; started+=spts
    player_rows.sort(key=lambda x:x["fsffl_points_while_rostered"], reverse=True)
    return {
        "captured_fsffl_points":round(total,2),
        "captured_started_points":round(started,2),
        "player_rows":player_rows,
        "methodology_note":"Trade-acquired lineage players are counted only from their actual acquisition transaction forward; drafted descendants use FSFFL points while rostered after their draft.",
    }


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


def build_asset_lineage(root_created: int, root_transaction_id: str, uid: str, roster_id: str, root_assets, players):
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
                    "season":int(c.get("season") or 0) or None,
                    "event_type":"draft_selection",
                    "from_assets":[pick],"to_assets":[str(player_aid)],
                    "description":f"{_asset_label(pick,players,conversion_map)} became {c.get('player_name')} at pick {c.get('pick_no')}.",
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
                "season":int(c.get("season") or 0) or None,
                "event_type":"draft_selection",
                "from_assets":[pick],"to_assets":[player],
                "description":f"{_asset_label(pick,players,conversion_map)} became {c.get('player_name')} at pick {c.get('pick_no')}.",
                "attribution":"direct",
            })

    current_map=_current_intrinsic_map()
    production=_lineage_production(uid,roster_id,root_transaction_id,root_created,root_assets,events,conversions)

    return {
        "root_assets":[{"asset_key":a,"label":_asset_label(a,players,conversion_map)} for a in root_assets],
        "events":events,
        "terminal_assets":[
            {"asset_key":a,"label":_asset_label(a,players,conversion_map),"current_intrinsic_value":round(current_map.get(a,0.0),1)}
            for a in sorted(live)
        ],
        "terminal_current_intrinsic_value":round(sum(current_map.get(a,0.0) for a in live),1),
        "captured_production":production,
        "mixed_attribution_events":mixed,
        "methodology_note":"Downstream asset lineage is factual. Mixed-package returns are retained but explicitly marked mixed; exact economic attribution is not claimed.",
    }


def _player_fsffl_points_since(pid: str, start_season: int):
    total=0.0
    for season in range(int(start_season), datetime.now(timezone.utc).year + 1):
        rows=loadj(DATA / "stats" / "fsffl" / str(season) / "player_season_fsffl.json", [])
        row=next((x for x in rows if str(x.get("player_id"))==str(pid)),None)
        if row:
            total+=float(row.get("fsffl_points") or 0)
    return round(total,2)


def keep_assets_reference(transaction_id: str, sent_assets: Dict[str, Any], players):
    """Observed reference for assets surrendered, not a fictional alternate-history replay."""
    perf=loadj(DATA / "transaction_performance_index.json", [])
    conversions=loadj(DATA / "draft_pick_conversion_index.json", [])
    conv={str(x.get("pick_asset_key")):x for x in conversions if x.get("pick_asset_key")}
    current=_current_intrinsic_map()
    rows=[]
    for pid in sent_assets.get("sent_players") or []:
        hit=next((r for r in perf if str(r.get("transaction_id"))==str(transaction_id) and str(r.get("player_id"))==str(pid)),None)
        p=players.get(str(pid)) or {}
        aid=f"player:{pid}"
        rows.append({
            "asset_key":aid,
            "label":str(p.get("full_name") or p.get("name") or pid),
            "observed_post_trade_points":round(float((hit or {}).get("fsffl_points_for_acquirer_after_trade") or 0),2),
            "current_intrinsic_value":round(current.get(aid,0.0),1),
            "reference_type":"observed_player_output_after_trade",
        })
    for aid in sent_assets.get("sent_picks") or []:
        c=conv.get(str(aid))
        player_aid=str((c or {}).get("player_asset_key") or "")
        draft_season=int((c or {}).get("season") or 0)
        pid=str((c or {}).get("player_id") or "")
        rows.append({
            "asset_key":str(aid),
            "label":_asset_label(str(aid),players,conv),
            "drafted_player":(c or {}).get("player_name"),
            "pick_no":(c or {}).get("pick_no"),
            "observed_drafted_player_points":_player_fsffl_points_since(pid,draft_season) if pid and draft_season else 0.0,
            "current_intrinsic_value":round(current.get(player_aid,0.0),1) if player_aid else 0.0,
            "reference_type":"actual_slot_conversion" if c else "unresolved_or_future_pick",
        })
    observed_points=sum(
        float(x.get("observed_post_trade_points") or x.get("observed_drafted_player_points") or 0)
        for x in rows
    )
    current_value=sum(float(x.get("current_intrinsic_value") or 0) for x in rows)
    return {
        "assets":rows,
        "observed_reference_points":round(observed_points,2),
        "current_reference_intrinsic_value":round(current_value,1),
        "note":"This is a keep-the-original-assets reference, not a claim about exact alternate history. For surrendered picks it shows the player actually selected at that slot and that player's observed FSFFL production. The original manager might have drafted differently if the trade never happened.",
    }


def hindsight_assessment(sides: Dict[str, Any]):
    """Compare realized outcomes without inventing a single points-plus-value score."""
    metrics={}
    for uid,side in sides.items():
        h=side.get("hindsight") or {}
        lin=h.get("asset_lineage") or {}
        keep=h.get("keep_assets_reference") or {}
        prod=float(((lin.get("captured_production") or {}).get("captured_fsffl_points")) or 0)
        terminal=float(lin.get("terminal_current_intrinsic_value") or 0)
        kp=float(keep.get("observed_reference_points") or 0)
        kv=float(keep.get("current_reference_intrinsic_value") or 0)
        if prod>kp*1.10 and terminal>=kv*.90:
            keep_result="OUTPERFORMED_KEEP_REFERENCE"
        elif terminal>kv*1.10 and prod>=kp*.90:
            keep_result="OUTPERFORMED_KEEP_REFERENCE"
        elif prod<kp*.90 and terminal<kv*.90:
            keep_result="UNDERPERFORMED_KEEP_REFERENCE"
        else:
            keep_result="MIXED_VS_KEEP_REFERENCE"
        metrics[str(uid)]={
            "captured_lineage_points":round(prod,2),
            "terminal_current_intrinsic_value":round(terminal,1),
            "keep_reference_points":round(kp,2),
            "keep_reference_current_intrinsic_value":round(kv,1),
            "vs_keep_reference":keep_result,
        }

    uids=list(metrics)
    if len(uids)!=2:
        return {"classification":"INSUFFICIENT_SIDES","winner_user_id":None,"metrics":metrics}
    a,b=uids
    ma,mb=metrics[a],metrics[b]
    pa,pb=ma["captured_lineage_points"],mb["captured_lineage_points"]
    va,vb=ma["terminal_current_intrinsic_value"],mb["terminal_current_intrinsic_value"]
    pclose=abs(pa-pb)<=0.10*max(pa,pb,1.0)
    vclose=abs(va-vb)<=0.10*max(va,vb,1.0)
    if pclose and vclose:
        classification="NEAR_EVEN_HINDSIGHT"; winner=None
    elif pa>=pb and va>=vb and (pa>pb or va>vb):
        classification="CLEAR_HINDSIGHT_EDGE"; winner=a
    elif pb>=pa and vb>=va and (pb>pa or vb>va):
        classification="CLEAR_HINDSIGHT_EDGE"; winner=b
    else:
        classification="SPLIT_HINDSIGHT_RESULT"; winner=None
    return {
        "classification":classification,
        "winner_user_id":winner,
        "metrics":metrics,
        "methodology_note":"A hindsight winner is declared only when one side leads on both actual lineage production and remaining descendant value. Split dimensions remain a split result rather than being forced into one synthetic score.",
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
                int(tx.get("created") or 0),str(transaction_id),uid,rid,root_assets,players
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
        "hindsight_assessment": hindsight_assessment(sides),
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
            "hindsight_winner_requires_dominance_on_production_and_remaining_value": True,
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
