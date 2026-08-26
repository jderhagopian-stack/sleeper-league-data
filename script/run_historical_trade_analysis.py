#!/usr/bin/env python3
"""Historical FSFFL trade analysis using point-in-time league state.

This module intentionally separates two questions:
1. Decision quality at the time, using only information available before the trade.
2. Realized outcome afterward, which is reported separately and never leaks into (1).

The canonical pre-trade state comes from FSFFL-Historical-State-Provider-1.0,
which was extracted from the validated Alternate History reconstruction.  The
context layer reuses the same conservative prior-completed-season player-quality
logic used by Behavioral Intelligence 3.0.

Version 1.0 is an evidence-first historical audit.  It does NOT invent historical
market values that were not archived.  Where exact historical valuation inputs
are unavailable, the decision grade is based on roster fit, prior-season quality,
pick-capital structure, and contemporaneous roster construction, with an explicit
confidence penalty and a limitations block.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fsffl_historical_state_provider import HistoricalStateProvider, completed_transactions, roster_to_user
from build_behavioral_action_context import player_index, prior_season_quality, summarize_roster, owner_directory

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MODEL_VERSION = "FSFFL-GM-Historical-Trade-Analysis-1.0"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def find_trade(provider: HistoricalStateProvider, season: str, transaction_id: str) -> dict[str, Any]:
    for tx in completed_transactions(provider.data(str(season))):
        if str(tx.get("transaction_id") or "") == str(transaction_id):
            if str(tx.get("type") or "") != "trade":
                raise ValueError(f"Transaction {transaction_id} is not a trade")
            return tx
    raise KeyError(f"Trade {season}/{transaction_id} not found")


def asset_names(players: dict[str, Any], ids: list[str]) -> list[str]:
    out = []
    for pid in ids:
        p = players.get(str(pid)) or {}
        out.append(str(p.get("full_name") or p.get("name") or pid))
    return out


def side_assets(tx: dict[str, Any], rid: str) -> dict[str, Any]:
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


def prior_quality_score(pid: str, season: int, qidx: dict[int, dict[str, Any]]) -> float | None:
    q = (qidx.get(int(season) - 1) or {}).get(str(pid))
    if q is None:
        return None
    # 0..1, higher is better. Uses only prior completed season.
    return float(q.get("position_percentile") or 0.0)


def pick_weight(asset_id: str, trade_season: int) -> float:
    # Structural capital only; no claim this is a reconstructed historical market price.
    try:
        _, year, rnd, _ = asset_id.split(":", 3)
        rnd_n = int(rnd.lstrip("R"))
        years_out = max(0, int(year) - int(trade_season))
    except Exception:
        return 0.0
    base = {1: 1.0, 2: 0.48, 3: 0.22}.get(rnd_n, 0.08)
    return base * (0.88 ** years_out)


def need_fit(roster_summary: dict[str, Any], acquired: list[str], players: dict[str, Any]) -> float | None:
    vals = []
    need = roster_summary.get("position_need") or {}
    for pid in acquired:
        pos = str((players.get(str(pid)) or {}).get("position") or "")
        if pos in need:
            vals.append(float(need[pos]))
    return sum(vals) / len(vals) if vals else None


def process_score(side: dict[str, Any], roster_summary: dict[str, Any], season: int,
                  players: dict[str, Any], qidx: dict[int, dict[str, Any]]) -> dict[str, Any]:
    acquired = side["received_players"]
    sent = side["sent_players"]
    aq = [prior_quality_score(x, season, qidx) for x in acquired]
    sq = [prior_quality_score(x, season, qidx) for x in sent]
    aq_known = [x for x in aq if x is not None]
    sq_known = [x for x in sq if x is not None]
    player_quality_delta = (sum(aq_known) / len(aq_known) if aq_known else 0.0) - (sum(sq_known) / len(sq_known) if sq_known else 0.0)
    pick_delta = sum(pick_weight(x, season) for x in side["received_picks"]) - sum(pick_weight(x, season) for x in side["sent_picks"])
    fit = need_fit(roster_summary, acquired, players)
    fit_component = 0.0 if fit is None else (float(fit) - 0.5)

    # Evidence-first composite.  Deliberately moderate weights because exact historical market values are not reconstructed.
    raw = 42.0 * player_quality_delta + 28.0 * pick_delta + 30.0 * fit_component
    quality_known = len(aq_known) + len(sq_known)
    quality_total = len(aq) + len(sq)
    coverage = quality_known / quality_total if quality_total else 1.0
    context_coverage = float(roster_summary.get("quality_coverage") or 0.0)
    confidence = max(0.25, min(1.0, 0.55 * coverage + 0.45 * context_coverage))
    adjusted = raw * (0.65 + 0.35 * confidence)

    if adjusted >= 18:
        grade, label = "A", "Strong process"
    elif adjusted >= 8:
        grade, label = "B", "Good process"
    elif adjusted > -8:
        grade, label = "C", "Reasonable / mixed process"
    elif adjusted > -18:
        grade, label = "D", "Questionable process"
    else:
        grade, label = "F", "Poor process"

    return {
        "grade": grade,
        "label": label,
        "score": round(adjusted, 2),
        "confidence": round(confidence, 3),
        "components": {
            "prior_completed_season_player_quality_delta": round(player_quality_delta, 4),
            "structural_pick_capital_delta": round(pick_delta, 4),
            "acquisition_need_fit": None if fit is None else round(fit, 4),
            "player_quality_coverage": round(coverage, 4),
            "pretrade_roster_quality_coverage": round(context_coverage, 4),
        },
        "uses_same_season_future_results": False,
        "exact_historical_market_value_used": False,
    }


def realized_outcome(transaction_id: str, uid: str) -> dict[str, Any]:
    rows = loadj(DATA / "transaction_performance_index.json", [])
    hits = [r for r in rows if str(r.get("transaction_id") or "") == str(transaction_id)
            and str(r.get("acquiring_user_id") or "") == str(uid)]
    return {
        "player_acquisition_rows": hits,
        "acquired_player_fsffl_points_after_trade": round(sum(float(r.get("fsffl_points_for_acquirer_after_trade") or 0) for r in hits), 2),
        "tracked_player_count": len(hits),
        "note": "Outcome is descriptive hindsight and is excluded from the decision-quality grade.",
    }


def analyze(season: str, transaction_id: str) -> dict[str, Any]:
    provider = HistoricalStateProvider()
    tx = find_trade(provider, str(season), str(transaction_id))
    state = provider.pre_transaction_state(str(season), str(transaction_id))
    data = provider.data(str(season))
    players = player_index()
    qidx = prior_season_quality(players)
    r2u = roster_to_user(data)
    owners = owner_directory(data)

    touched = {str(x) for x in (tx.get("roster_ids") or [])}
    touched |= {str(x) for x in (tx.get("adds") or {}).values() if x is not None}
    touched |= {str(x) for x in (tx.get("drops") or {}).values() if x is not None}

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
            "decision_quality_at_time": process_score(assets, summary, int(season), players, qidx),
            "realized_outcome": realized_outcome(str(transaction_id), uid),
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
        "policy": {
            "decision_quality_uses_only_information_available_before_trade": True,
            "same_season_future_results_leakage_forbidden": True,
            "outcome_grade_separate_from_process_grade": True,
            "exact_historical_market_values_are_not_invented": True,
            "current_player_values_not_backfilled_into_historical_grade": True,
            "alternate_history_state_provider_reused": True,
        },
        "sides": sides,
        "limitations": [
            "Exact historical external dynasty-market values are not yet archived for every trade date, so v1.0 does not fabricate them.",
            "Decision grades are evidence-first composites of prior-season player quality, structural pick capital, and contemporaneous roster need.",
            "Realized outcomes are reported separately and never alter the at-the-time process grade.",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    ap.add_argument("--transaction-id", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    result = analyze(args.season, args.transaction_id)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "model_version": result["model_version"],
        "transaction_id": result["transaction_id"],
        "trade_time_utc": result["trade_time_utc"],
        "teams": {uid: {"team": r["team_name"], "grade": r["decision_quality_at_time"]["grade"], "confidence": r["decision_quality_at_time"]["confidence"]} for uid, r in result["sides"].items()},
    }, indent=2))


if __name__ == "__main__":
    main()
