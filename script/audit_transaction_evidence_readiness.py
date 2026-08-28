#!/usr/bin/env python3
"""Inventory FSFFL transaction evidence for package/acceptance calibration.

Completed transaction history can describe trade geometry and manager actions.
It cannot, by itself, identify a package discount relative to contemporaneous
market value or an acceptance probability without the corresponding value
snapshots/opportunity denominator. This audit keeps those claims separate.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Transaction-Evidence-Readiness-1.0"


def load(path: Path, default):
    if not path.exists() or path.stat().st_size == 0:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def list_count(row, names):
    n = 0
    used = []
    for name in names:
        v = row.get(name)
        if isinstance(v, list):
            n += len(v)
            if v:
                used.append(name)
    return n, used


def has_frozen_value_fields(row):
    keys = {str(k).lower() for k in row}
    required_families = (
        ("market_value", "dynasty_value", "fantasycalc_value", "ktc_value"),
        ("value_timestamp", "market_timestamp", "valuation_as_of", "value_as_of"),
    )
    return all(any(token in keys for token in family) for family in required_families)


def main():
    ledger = load(DATA / "acquisition_ledger.json", [])
    if not isinstance(ledger, list):
        ledger = []
    manifest = load(DATA / "behavioral" / "manifest.json", {})

    types = Counter(str(r.get("type") or "UNKNOWN").lower() for r in ledger)
    completed = [r for r in ledger if str(r.get("status") or "").lower() == "complete"]
    trade_rows = [r for r in completed if str(r.get("type") or "").lower() == "trade"]
    by_tx = defaultdict(list)
    for r in trade_rows:
        by_tx[str(r.get("transaction_id") or f"missing:{len(by_tx)}")].append(r)

    asset_list_names = (
        "players_added", "players_dropped", "players", "adds", "drops",
        "draft_picks_added", "draft_picks_dropped", "picks_added", "picks_dropped",
        "draft_picks", "picks",
    )
    geometry = Counter()
    pick_key_usage = Counter()
    frozen_value_rows = 0
    timestamped_trade_rows = 0
    for txid, rows in by_tx.items():
        max_side_assets = 0
        total_list_assets = 0
        for r in rows:
            cnt, used = list_count(r, asset_list_names)
            total_list_assets += cnt
            max_side_assets = max(max_side_assets, cnt)
            for k in used:
                if "pick" in k:
                    pick_key_usage[k] += 1
            if r.get("created") or r.get("created_utc"):
                timestamped_trade_rows += 1
            if has_frozen_value_fields(r):
                frozen_value_rows += 1
        if max_side_assets <= 1:
            bucket = "one_or_zero_list_assets_max_side"
        elif max_side_assets == 2:
            bucket = "two_list_assets_max_side"
        elif max_side_assets == 3:
            bucket = "three_list_assets_max_side"
        else:
            bucket = "four_plus_list_assets_max_side"
        geometry[bucket] += 1

    behavioral_trade_actions = int((manifest.get("action_type_counts") or {}).get("TRADE") or 0)
    reconstructed_actions = int(manifest.get("reconstructed_action_count") or 0)

    package_ready = bool(by_tx) and frozen_value_rows == len(trade_rows) and len(trade_rows) > 0
    # No committed rejected-offer ledger is identified here. Behavioral action
    # reconstruction models choices among contextual opportunities, but the
    # manifest contains actions rather than a logged offer/accept-reject table.
    rejected_offer_denominator_present = False
    acceptance_probability_ready = False

    findings = [
        {
            "id": "PACKAGE-CALIBRATION-READINESS-001",
            "severity": "HIGH",
            "status": "READY_FOR_RESIDUAL_CALIBRATION" if package_ready else "GEOMETRY_ONLY_NOT_VALUE_RESIDUAL_READY",
            "observation": (
                "Completed trade history can describe package sizes. Estimating a league-specific consolidation/package discount requires each side's contemporaneous market/value baseline at the transaction timestamp; current-value backfill is not acceptable."
            ),
            "unique_completed_trade_transaction_count": len(by_tx),
            "trade_rows_with_embedded_frozen_value_and_timestamp_fields": frozen_value_rows,
            "authoritative_empirical_claim_allowed": package_ready,
        },
        {
            "id": "ACCEPTANCE-CALIBRATION-READINESS-001",
            "severity": "HIGH",
            "status": "CHOICE_CONTEXT_AVAILABLE_NO_ACCEPT_REJECT_DENOMINATOR",
            "observation": (
                "Behavioral action reconstruction is useful for manager preference evidence, but completed actions alone do not identify the probability that a proposed trade is accepted. A logged offer/opportunity set containing both accepted and rejected/expired proposals, or another defensible choice target, is required before calibrating acceptance probability."
            ),
            "behavioral_reconstructed_action_count": reconstructed_actions,
            "behavioral_trade_action_count": behavioral_trade_actions,
            "rejected_offer_denominator_present": rejected_offer_denominator_present,
            "authoritative_empirical_claim_allowed": acceptance_probability_ready,
        },
    ]

    payload = {
        "model_version": MODEL_VERSION,
        "purpose": "Distinguish descriptive transaction history from the evidence needed to estimate package economics and acceptance probability.",
        "summary": {
            "ledger_row_count": len(ledger),
            "completed_row_count": len(completed),
            "transaction_types": dict(types),
            "completed_trade_row_count": len(trade_rows),
            "unique_completed_trade_transaction_count": len(by_tx),
            "timestamped_trade_row_count": timestamped_trade_rows,
            "trade_rows_with_embedded_frozen_value_fields": frozen_value_rows,
            "behavioral_reconstructed_action_count": reconstructed_actions,
            "behavioral_trade_action_count": behavioral_trade_actions,
            "package_geometry": dict(geometry),
            "pick_list_key_usage": dict(pick_key_usage),
        },
        "policy": {
            "current_market_value_backfill_for_historical_package_fit_forbidden": True,
            "completed_trades_alone_are_not_acceptance_probability_denominator": True,
            "geometry_description_is_not_package_value_calibration": True,
        },
        "findings": findings,
    }
    (OUT / "transaction_evidence_readiness_audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    print(json.dumps(findings, indent=2))


if __name__ == "__main__":
    main()
