#!/usr/bin/env python3
"""Diagnose FAAB trades excluded for missing principal coordinates.

Research-only reporting helper. It classifies why a principal side could not be
reconstructed and surfaces the exact historical asset keys involved. It does not
backfill values, use future information, or change any production model behavior.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "audit"
SOURCE = OUT / "historical_faab_nuisance_sensitivity.json"
LEDGER = DATA / "trade_ledger.json"
TARGET = OUT / "historical_faab_missing_principal_diagnostic.json"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def principal_assets(side):
    assets = []
    for p in side.get("sent_players") or []:
        assets.append({
            "type": "player",
            "key": "player:" + str(p.get("player_id") or ""),
            "name": p.get("player_name") or p.get("name"),
        })
    for p in side.get("sent_picks") or []:
        assets.append({
            "type": "pick",
            "key": "pick:%s:R%s:orig%s" % (
                p.get("season"), p.get("round"), p.get("original_roster_id")
            ),
            "name": p.get("description") or p.get("label"),
        })
    return assets


def main() -> None:
    faab = loadj(SOURCE, {}) or {}
    ledger = loadj(LEDGER, []) or []
    trades = {str(t.get("transaction_id") or ""): t for t in ledger}
    blocked = [
        r for r in (faab.get("trades") or [])
        if r.get("status") == "MISSING_PRINCIPAL_COORDINATE"
    ]

    by_season = Counter()
    missing_key_family = Counter()
    cause_counts = Counter()
    missing_key_counts = Counter()
    details = []

    for row in blocked:
        tid = str(row.get("transaction_id") or "")
        trade = trades.get(tid) or {}
        season = str(row.get("season") or trade.get("season") or "unknown")
        by_season[season] += 1
        missing = [str(x) for x in (row.get("missing") or [])]
        for key in missing:
            missing_key_counts[key] += 1
            missing_key_family["pick" if key.startswith("pick:") else "player" if key.startswith("player:") else "other"] += 1

        sides = trade.get("sides") or []
        side_assets = [principal_assets(side) for side in sides]
        if missing:
            if all(k.startswith("player:") for k in missing):
                cause = "MISSING_HISTORICAL_PLAYER_COORDINATE"
            elif all(k.startswith("pick:") for k in missing):
                cause = "MISSING_HISTORICAL_PICK_COORDINATE"
            else:
                cause = "MIXED_MISSING_COORDINATES"
        elif any(len(a) == 0 for a in side_assets):
            cause = "ONE_SIDE_HAS_NO_PRINCIPAL_ASSET"
        else:
            cause = "RECONSTRUCTED_SIDE_EMPTY_WITHOUT_REPORTED_MISSING_KEY"
        cause_counts[cause] += 1

        details.append({
            "transaction_id": tid,
            "season": row.get("season") or trade.get("season"),
            "created_utc": trade.get("created_utc"),
            "cause": cause,
            "missing_keys": missing,
            "side_principal_assets": side_assets,
            "faab_sent": [side.get("faab_sent") for side in sides],
        })

    output = {
        "research_only": True,
        "production_authority": False,
        "production_behavior_changed": False,
        "historical_value_backfill_performed": False,
        "future_information_used": False,
        "missing_principal_trade_count": len(blocked),
        "by_season": dict(by_season),
        "cause_counts": dict(cause_counts),
        "missing_key_family_counts": dict(missing_key_family),
        "missing_key_counts": dict(missing_key_counts),
        "trades": details,
        "interpretation_guardrail": (
            "A missing coordinate remains missing unless contemporaneous evidence supports reconstruction. "
            "This diagnostic is for recoverability triage and cannot justify current-value or hindsight backfill."
        ),
    }
    TARGET.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "missing_principal_trade_count": output["missing_principal_trade_count"],
        "by_season": output["by_season"],
        "cause_counts": output["cause_counts"],
        "missing_key_family_counts": output["missing_key_family_counts"],
    }, indent=2))

    assert output["production_authority"] is False
    assert output["historical_value_backfill_performed"] is False
    assert output["future_information_used"] is False


if __name__ == "__main__":
    main()
