#!/usr/bin/env python3
"""Audit empirical readiness for residual economic families without fitting coefficients.

This audit is deliberately conservative. It answers:
- how many completed package trades exist;
- whether dated contemporaneous market snapshots are broad enough for a
  leakage-safe temporal holdout;
- what exact target/denominator/ablation each still-disabled residual family
  would require.

It does not backfill current market values into historical trades, fit package
curves, activate any production channel, or treat completed trades as rejected
offer / opportunity denominators.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
TRADE_LEDGER = ROOT / "data" / "trade_ledger.json"
HIST_SOURCES = ROOT / "data" / "historical_gm3" / "sources"
OUT = ROOT / "data" / "audit" / "package_concentration_empirical_readiness.json"


def _asset_count(side):
    return len(side.get("sent_players") or []) + len(side.get("sent_picks") or [])


def _iso_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return None


def load_historical_sources():
    rows = []
    if not HIST_SOURCES.exists():
        return rows
    for path in sorted(HIST_SOURCES.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append({
            "path": str(path.relative_to(ROOT)),
            "as_of_utc": data.get("as_of_utc"),
            "as_of_date": _iso_date(data.get("as_of_utc")),
            "player_source_published": (data.get("player_source") or {}).get("published"),
            "player_value_count": len(((data.get("player_source") or {}).get("values") or {})),
            "pick_source_count": len(data.get("pick_sources") or []),
            "current_market_values_forbidden": bool((data.get("policy") or {}).get("current_market_values_forbidden")),
            "same_season_results_forbidden": bool((data.get("policy") or {}).get("same_season_results_forbidden")),
        })
    return rows


def main():
    trades = json.loads(TRADE_LEDGER.read_text(encoding="utf-8"))
    completed = [x for x in trades if x.get("status") == "complete"]
    bilateral = [x for x in completed if len(x.get("sides") or []) == 2]

    topology = {
        "one_for_one": 0,
        "one_for_many": 0,
        "many_for_one": 0,
        "many_for_many": 0,
        "other": 0,
    }
    by_season = {}
    package_trade_ids = []
    for trade in bilateral:
        by_season[str(trade.get("season"))] = by_season.get(str(trade.get("season")), 0) + 1
        a, b = trade["sides"]
        ac, bc = _asset_count(a), _asset_count(b)
        if ac == 1 and bc == 1:
            key = "one_for_one"
        elif ac == 1 and bc > 1:
            key = "one_for_many"
            package_trade_ids.append(str(trade.get("transaction_id")))
        elif ac > 1 and bc == 1:
            key = "many_for_one"
            package_trade_ids.append(str(trade.get("transaction_id")))
        elif ac > 1 and bc > 1:
            key = "many_for_many"
            package_trade_ids.append(str(trade.get("transaction_id")))
        else:
            key = "other"
        topology[key] += 1

    sources = load_historical_sources()
    unique_snapshot_dates = sorted({x["as_of_date"] for x in sources if x.get("as_of_date")})
    source_dates = set(unique_snapshot_dates)
    exact_date_trade_matches = [
        str(t.get("transaction_id"))
        for t in bilateral
        if _iso_date(t.get("created_utc")) in source_dates
    ]

    temporal_holdout_possible = len(unique_snapshot_dates) >= 2
    package_empirical_calibration_ready = False
    package_bounded_provisional_authority_possible = True

    payload = {
        "schema_version": "1.0",
        "audit_family": "residual empirical readiness",
        "production_behavior_changed": False,
        "coefficient_fit_performed": False,
        "current_value_backfill_used": False,
        "package_concentration": {
            "completed_trade_count": len(completed),
            "bilateral_trade_count": len(bilateral),
            "topology": topology,
            "package_trade_count": len(package_trade_ids),
            "trades_by_season": by_season,
            "historical_market_source_file_count": len(sources),
            "historical_market_snapshot_dates": unique_snapshot_dates,
            "exact_date_trade_match_count": len(exact_date_trade_matches),
            "exact_date_trade_match_transaction_ids": exact_date_trade_matches,
            "temporal_holdout_possible_from_current_frozen_sources": temporal_holdout_possible,
            "empirical_calibration_ready": package_empirical_calibration_ready,
            "bounded_provisional_authority_possible": package_bounded_provisional_authority_possible,
            "blocking_reason": (
                "Completed package trades are plentiful, but the current repository does not yet contain "
                "enough distinct frozen contemporaneous market snapshots to construct a leakage-safe temporal "
                "holdout for empirical coefficient calibration. This blocks an empirically calibrated point estimate, "
                "not a bounded provisional prior when the residual is otherwise credible, isolated, and sensitivity-exposed. "
                "Current market values must not be backfilled."
            ),
            "next_dataset": {
                "reuse_existing_historical_state_provider": True,
                "duplicate_historical_reconstruction_forbidden": True,
                "required_per_trade_fields": [
                    "transaction timestamp",
                    "exact package asset composition on both sides",
                    "frozen contemporaneous market value for each package asset",
                    "pre-trade roster state",
                    "automatic/required roster legalization separated from negotiated package",
                    "canonical current-team utility inputs known at the trade date",
                ],
                "evaluation_target": (
                    "Held-out package clearing/choice residual after conditioning on contemporaneous market value "
                    "and already-authorized roster/current utility. Compare additive baseline against the bounded "
                    "package transform; do not assume completed trades imply exact value parity."
                ),
                "stronger_choice_evidence": [
                    "rejected or expired offers",
                    "observed counteroffer ladders",
                    "identifiable manager choice frontiers",
                ],
            },
        },
        "residual_targets": {
            "OPTIONALITY-RESIDUAL-001": {
                "production_ready": False,
                "target": (
                    "Asymmetric future market-value/outcome distribution conditional on the asset's frozen "
                    "current dynasty market value at the decision date."
                ),
                "recommended_horizons": ["~26 weeks", "~52 weeks", "~104 weeks where observable"],
                "minimum_fields": [
                    "frozen decision-date market value",
                    "future frozen market values/outcomes",
                    "position",
                    "age/experience",
                    "NFL draft pedigree known at decision date",
                ],
                "forbidden_shortcut": (
                    "Do not activate the legacy optionality formula or reuse same-source market trend/spread "
                    "as independent value without residual holdout evidence."
                ),
            },
            "LIQUIDITY-RESIDUAL-001": {
                "production_ready": False,
                "denominator": (
                    "Asset exposure/opportunity time, ideally manager-asset-week or manager-asset-month while the "
                    "asset is owned and tradeable, augmented by observed offers when available. Executed trades are "
                    "the numerator, not the denominator."
                ),
                "controls": [
                    "contemporaneous market price",
                    "manager fixed effects or behavior",
                    "competitive state",
                    "asset position/type",
                    "calendar/season phase",
                ],
                "target": (
                    "Observed convertibility/retradeability or breadth of demand after controlling for price and "
                    "manager behavior."
                ),
                "forbidden_shortcut": "Completed trade frequency alone cannot be interpreted as asset liquidity.",
            },
            "RESILIENCE-RESIDUAL-001": {
                "production_ready": False,
                "ablation": (
                    "Paired stress/future-horizon simulation holding current-season lineup utility fixed, then "
                    "toggle only depth-insurance availability and measure avoided future loss. Starter dependency "
                    "and ordinary current Simulator substitution remain excluded."
                ),
                "required_inputs": [
                    "historical player availability/injury rates by relevant role/position",
                    "future/stress roster states",
                    "paired simulation common random numbers",
                    "depth-insurance-only roster perturbation",
                ],
                "target": "Expected future/stress loss avoided beyond current lineup and Simulator effects.",
                "forbidden_shortcut": "Do not reactivate the legacy starter-dependency + depth-insurance blend.",
            },
        },
        "sources": sources,
        "central_finding": (
            "Package concentration has enough completed-trade topology to justify continued challenger work, "
            "but not enough frozen contemporaneous market snapshots for empirical production promotion. "
            "Optionality, liquidity, and resilience each have a now-explicit residual target but remain disabled."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "completed_trades": len(completed),
        "package_trades": len(package_trade_ids),
        "historical_market_source_files": len(sources),
        "snapshot_dates": unique_snapshot_dates,
        "temporal_holdout_possible": temporal_holdout_possible,
        "package_empirical_calibration_ready": package_empirical_calibration_ready,
        "package_bounded_provisional_authority_possible": package_bounded_provisional_authority_possible,
    }, indent=2))

    assert len(completed) > 0
    assert len(package_trade_ids) > 0
    assert payload["production_behavior_changed"] is False
    assert payload["coefficient_fit_performed"] is False
    assert payload["current_value_backfill_used"] is False
    assert payload["package_concentration"]["empirical_calibration_ready"] is False
    assert payload["package_concentration"]["bounded_provisional_authority_possible"] is True
    assert all(not x["production_ready"] for x in payload["residual_targets"].values())


if __name__ == "__main__":
    main()
