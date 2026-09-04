#!/usr/bin/env python3
"""Classify historical FAAB transactions by role in the trade.

Research only. Distinguishes incidental FAAB from FAAB-as-price/liquidation so
package-concentration calibration does not treat them as the same economic event.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)


def loadj(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def n_principal(side):
    return len(side.get("sent_players") or []) + len(side.get("sent_picks") or [])


def main():
    trades = [t for t in (loadj(DATA / "trade_ledger.json", []) or [])
              if str(t.get("status") or "").lower() == "complete" and int(t.get("season") or 0) > 2022]
    rows = []
    counts = Counter()
    manager_counts = Counter()
    for t in trades:
        sides = t.get("sides") or []
        if len(sides) != 2:
            continue
        faab = [float(s.get("faab_sent") or 0) for s in sides]
        if max(faab or [0]) <= 0:
            continue
        pc = [n_principal(s) for s in sides]
        if pc == [0, 0]:
            archetype = "FAAB_ONLY_TRANSFER"
        elif (pc[0] == 0) ^ (pc[1] == 0):
            archetype = "PRINCIPAL_FOR_FAAB_LIQUIDATION"
        else:
            archetype = "INCIDENTAL_FAAB_IN_SUBSTANTIVE_EXCHANGE"
        counts[archetype] += 1
        managers = [str(s.get("manager") or s.get("user_id") or "unknown") for s in sides]
        for m in managers:
            manager_counts[(m, archetype)] += 1
        rows.append({
            "transaction_id": str(t.get("transaction_id") or ""),
            "season": int(t.get("season") or 0),
            "created_utc": t.get("created_utc"),
            "archetype": archetype,
            "principal_side_counts": pc,
            "faab_sent": faab,
            "managers": managers,
        })
    out = {
        "research_only": True,
        "production_authority": False,
        "counts": dict(counts),
        "manager_archetype_counts": {
            f"{m}::{a}": n for (m, a), n in sorted(manager_counts.items())
        },
        "trades": rows,
        "interpretation": {
            "INCIDENTAL_FAAB_IN_SUBSTANTIVE_EXCHANGE": "Eligible for bounded FAAB nuisance robustness testing.",
            "PRINCIPAL_FOR_FAAB_LIQUIDATION": "Treat as liquidation/roster-bubble evidence, not package-concentration evidence.",
            "FAAB_ONLY_TRANSFER": "Not package-concentration evidence."
        }
    }
    (OUT / "historical_faab_archetypes.json").write_text(json.dumps(out, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps({"counts": dict(counts)}, indent=2))


if __name__ == "__main__":
    main()
