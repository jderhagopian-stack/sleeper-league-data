#!/usr/bin/env python3
"""Leave-one-manager-out stability check for historical package calibration.

Research only. No manager is hard-coded as good/bad evidence. The analysis asks
whether the curve ordering depends disproportionately on any one manager's trades.
"""
from __future__ import annotations

import json
from collections import defaultdict
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


def aggregate(rows):
    curves = ("additive", "mild", "center", "strong")
    out = {}
    for c in curves:
        vals = []
        weights = []
        for r in rows:
            d = (r.get("absolute_clearing_distance") or {}).get(c)
            if d is None:
                continue
            w = float(r.get("evidence_weight") or 1.0)
            vals.append(float(d) * w)
            weights.append(w)
        out[c] = (sum(vals) / sum(weights)) if weights else None
    ranking = [c for c in curves if out[c] is not None]
    ranking.sort(key=lambda c: out[c])
    return out, ranking


def main():
    expanded = loadj(OUT / "historical_package_concentration_expanded.json", {}) or {}
    ledger = loadj(DATA / "trade_ledger.json", []) or []
    managers_by_tid = {}
    for t in ledger:
        tid = str(t.get("transaction_id") or "")
        managers_by_tid[tid] = sorted({str(s.get("manager") or s.get("user_id") or "unknown") for s in (t.get("sides") or [])})

    rows = [r for r in (expanded.get("trades") or []) if r.get("topology") != "ONE_FOR_ONE"]
    all_metrics, baseline = aggregate(rows)
    manager_rows = defaultdict(set)
    for r in rows:
        tid = str(r.get("transaction_id") or "")
        for m in managers_by_tid.get(tid, []):
            manager_rows[m].add(tid)

    results = []
    for manager, tids in sorted(manager_rows.items()):
        remaining = [r for r in rows if str(r.get("transaction_id") or "") not in tids]
        metrics, ranking = aggregate(remaining)
        results.append({
            "manager": manager,
            "removed_trade_count": len(tids),
            "remaining_trade_count": len(remaining),
            "ranking_without_manager": ranking,
            "baseline_ranking_changed": ranking != baseline,
            "curve_weighted_mean_without_manager": {k: (round(v,4) if v is not None else None) for k,v in metrics.items()},
        })

    out = {
        "research_only": True,
        "production_authority": False,
        "baseline_unequal_package_trade_count": len(rows),
        "baseline_ranking": baseline,
        "baseline_curve_weighted_mean": {k: (round(v,4) if v is not None else None) for k,v in all_metrics.items()},
        "leave_one_manager_out": results,
        "manager_specific_exclusion_rule_used": False,
        "interpretation": "Use as an influence diagnostic. A manager is not excluded merely because results change; material dependence triggers closer review of those transactions."
    }
    (OUT / "historical_manager_influence.json").write_text(json.dumps(out, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps({
        "baseline_ranking": baseline,
        "managers_tested": len(results),
        "ranking_changes": [r["manager"] for r in results if r["baseline_ranking_changed"]],
    }, indent=2))


if __name__ == "__main__":
    main()
