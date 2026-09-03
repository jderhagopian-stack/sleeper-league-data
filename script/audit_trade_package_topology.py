#!/usr/bin/env python3
"""Describe completed FSFFL trade package topology without fitting value coefficients.

The trade ledger is useful evidence that multi-asset-for-single-asset exchange is
a real league behavior, but it does not contain frozen contemporaneous market
values for every transaction. Therefore this audit measures topology only and
explicitly forbids using current values to back-fit historical package premiums.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "trade_ledger.json"
OUT = ROOT / "data" / "audit" / "trade_package_topology_audit.json"


def side_asset_count(side):
    return (
        len(side.get("received_players") or [])
        + len(side.get("received_picks") or [])
    )


def main():
    rows = json.loads(LEDGER.read_text(encoding="utf-8"))
    completed = [x for x in rows if str(x.get("status") or "").lower() == "complete" and len(x.get("sides") or []) == 2]

    seasons = Counter()
    patterns = Counter()
    directional = []
    for trade in completed:
        sides = trade.get("sides") or []
        a, b = side_asset_count(sides[0]), side_asset_count(sides[1])
        if a == 0 or b == 0:
            # FAAB-only or malformed transactions are not package-economics evidence.
            continue
        seasons[str(trade.get("season") or "unknown")] += 1
        lo, hi = sorted((a, b))
        if lo == 1 and hi == 1:
            patt = "ONE_FOR_ONE"
        elif lo == 1 and hi >= 2:
            patt = "ONE_FOR_MANY"
        elif lo >= 2 and hi >= 2:
            patt = "MANY_FOR_MANY"
        else:
            patt = "OTHER"
        patterns[patt] += 1
        directional.append({
            "transaction_id": str(trade.get("transaction_id") or ""),
            "season": str(trade.get("season") or ""),
            "asset_counts": [a, b],
            "pattern": patt,
        })

    package_trades = sum(patterns.values())
    one_many = patterns["ONE_FOR_MANY"]
    payload = {
        "schema_version": "1.0",
        "audit_family": "completed trade package topology",
        "production_behavior_changed": False,
        "coefficient_fit_performed": False,
        "current_value_backfill_used": False,
        "policy": {
            "completed_trade_topology_supports_concept_existence_not_coefficient_magnitude": True,
            "historical_current_value_backfill_for_premium_fit_forbidden": True,
            "frozen_contemporaneous_values_required_for_empirical_package_curve_fit": True,
            "accepted_trades_alone_do_not_identify_rejected_offer_frontier": True,
        },
        "summary": {
            "completed_two_sided_transactions": len(completed),
            "package_trades_with_nonzero_assets_both_sides": package_trades,
            "one_for_one": patterns["ONE_FOR_ONE"],
            "one_for_many": one_many,
            "many_for_many": patterns["MANY_FOR_MANY"],
            "one_for_many_share": round(one_many / package_trades, 4) if package_trades else 0.0,
            "season_counts": dict(sorted(seasons.items())),
        },
        "interpretation": (
            "Observed one-for-many completed trades demonstrate that aggregation/consolidation is a real "
            "exchange topology in this league. They do not by themselves estimate the premium required, "
            "because rejected offers and frozen contemporaneous values are incomplete."
        ),
        "transactions": directional,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
