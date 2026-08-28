#!/usr/bin/env python3
"""Historical FSFFL trade analysis as a time-travel wrapper around GM 3.0.

Historical Trade Analysis does not own a separate valuation or grading formula.
Its responsibilities are:
1. reconstruct the exact pre-trade league state,
2. load archived-at-time GM3 inputs when available, otherwise reconstruct them from timestamp-safe evidence,
3. delegate the decision evaluation to the canonical GM3/Decision Lab core,
4. report realized outcome afterward as a strictly separate hindsight layer.

Missing contemporaneous archives do not disable the feature: the module can
reconstruct a point-in-time bundle from historical roster state, prior completed
season production, pre-trade manager behavior, and dated external anchors when
available. Reconstructed grades are labeled for audit/backtest purposes and are
not counted as pristine out-of-sample forecasts.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
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


def default_bundle_path(season: str, transaction_id: str) -> Path:
    return DATA / "historical_gm3" / str(season) / f"{transaction_id}.json"


def dated_market_source_at_or_before(timestamp_ms: int) -> Path | None:
    root = DATA / "historical_gm3" / "sources"
    if not root.exists():
        return None
    when = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc).date()
    candidates = []
    for path in root.glob("*.json"):
        try:
            d = datetime.strptime(path.name[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if d <= when:
            candidates.append((d, path))
    return max(candidates, default=(None, None), key=lambda x: x[0] or datetime.min.date())[1]


def reconstruct_bundle(season: str, transaction_id: str, timestamp_ms: int):
    builder = load_module(SCRIPT / "build_historical_gm3_bundle.py", "historical_gm3_reconstructor")
    source = dated_market_source_at_or_before(timestamp_ms)
    return builder.build(str(season), str(transaction_id), source), source


def analyze(season: str, transaction_id: str, sims=1000, seed=20260821, bundle_path: str | None = None):
    provider = HistoricalStateProvider()
    tx = find_trade(provider, str(season), str(transaction_id))
    state = provider.pre_transaction_state(str(season), str(transaction_id))
    data = provider.data(str(season))
    players = player_index()
    qidx = prior_season_quality(players)
    r2u = roster_to_user(data)
    owners = owner_directory(data)
    actions = transaction_actions(tx, data)

    touched = {str(x) for x in (tx.get("roster_ids") or [])}
    touched |= {str(x) for x in (tx.get("adds") or {}).values() if x is not None}
    touched |= {str(x) for x in (tx.get("drops") or {}).values() if x is not None}
    participant_uids = sorted({r2u[rid] for rid in touched if rid in r2u})

    sides = {}
    for rid in sorted(touched):
        uid = r2u.get(rid)
        if not uid:
            continue
        assets = side_assets(tx, rid)
        summary = summarize_roster(state.roster_players.get(rid, set()), int(season), players, qidx)
        sides[uid] = {
            "user_id": uid,
            "roster_id": rid,
            "manager": (owners.get(uid) or {}).get("manager"),
            "team_name": (owners.get(uid) or {}).get("team_name"),
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

    requested = Path(bundle_path) if bundle_path else default_bundle_path(str(season), str(transaction_id))
    reconstructed_source = None
    if requested.exists():
        bundle = loadj(requested, None)
        bundle_origin = "ARCHIVED_FILE"
    else:
        bundle, reconstructed_source = reconstruct_bundle(str(season), str(transaction_id), int(tx.get("created") or 0))
        bundle_origin = "RECONSTRUCTED_AT_TIME"
    adapter = load_module(SCRIPT / "historical_trade_gm3_adapter.py", "historical_trade_gm3_adapter")
    gm3 = adapter.evaluate(
        state, data, actions, participant_uids, bundle, sims=int(sims), seed=int(seed)
    )

    for uid, side in sides.items():
        side["gm3_decision_at_time"] = (gm3.get("team_results") or {}).get(uid) if str(gm3.get("status") or "").startswith("GRADED_") else {
            "status": gm3.get("status") or "INSUFFICIENT_POINT_IN_TIME_INPUTS",
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
        },
        "actions": actions,
        "participant_user_ids": participant_uids,
        "gm3_evaluation": gm3,
        "historical_input_basis": gm3.get("historical_input_class") or bundle_origin,
        "strict_out_of_sample_backtest_eligible": bool(gm3.get("strict_out_of_sample_backtest_eligible")),
        "historical_bundle_path": str(requested.relative_to(ROOT)) if requested.exists() and requested.is_absolute() and str(requested).startswith(str(ROOT)) else (str(requested) if requested.exists() else None),
        "reconstructed_market_source": str(reconstructed_source.relative_to(ROOT)) if reconstructed_source else None,
        "sides": sides,
        "policy": {
            "historical_module_is_time_travel_wrapper_not_scoring_model": True,
            "same_gm3_core_as_current_trade_analysis": True,
            "decision_quality_uses_only_information_available_before_trade": True,
            "same_season_future_results_leakage_forbidden": True,
            "outcome_layer_separate_from_decision_layer": True,
            "current_player_values_not_backfilled_into_historical_grade": True,
            "standalone_v1_process_score_retired": True,
            "missing_archived_inputs_do_not_disable_historical_analysis": True,
            "reconstructed_at_time_inputs_are_allowed": True,
            "reconstructed_at_time_grades_are_not_pristine_backtest_observations": True,
            "alternate_history_state_provider_reused": True,
        },
        "limitations": [
            "Archived-at-time bundles are preferred for strict empirical backtesting.",
            "Reconstructed-at-time bundles preserve decision functionality but may inherit later model methodology, so they are excluded from pristine out-of-sample accuracy claims.",
            "Reconstruction provenance and confidence belong in report methodology/end notes unless they materially alter the recommendation.",
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
