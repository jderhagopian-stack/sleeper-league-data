#!/usr/bin/env python3
"""Audit contender-state utility sensitivity on real Opportunity Engine trades.

This diagnostic does not calibrate or change objective weights. It measures how
sensitive each trade's Shared Decision Utility is to the governed current/future
state-weight curve and identifies trades whose sign depends on elite-contender
weighting.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

ANCHORS = [
    ("rebuild_anchor", 0.08, 0.62),
    ("retool_anchor", 0.23, 0.47),
    ("contender_anchor", 0.40, 0.35),
    ("elite_contender_anchor", 0.50, 0.25),
    ("maximum_competitive_anchor", 0.56, 0.21),
]

def _channel(row, name):
    for x in ((row.get("decision_attribution") or {}).get("channels") or []):
        if x.get("channel") == name:
            return x
    return {}

def _score(current, future, current_raw, future_raw):
    total = float(current_raw) + float(future_raw)
    if total <= 0:
        return None
    w = float(current_raw) / total
    return w * float(current) + (1.0 - w) * float(future)

def audit(board):
    rows = [
        r for r in (board.get("ranked_single_step_opportunities") or [])
        if r.get("channel") == "TRADE"
    ]
    out = []
    for row in rows:
        cur = _channel(row, "current")
        fut = _channel(row, "future")
        c = float(cur.get("primitive_value") or 0.0)
        f = float(fut.get("primitive_value") or 0.0)
        actual_w = float(cur.get("objective_weight") or 0.0)
        breakeven = None
        if c != f:
            breakeven = (-f) / (c - f)
        anchor_scores = {
            name: round(_score(c, f, cw, fw), 2)
            for name, cw, fw in ANCHORS
        }
        contender_positive = anchor_scores["contender_anchor"] > 0
        elite_positive = anchor_scores["elite_contender_anchor"] > 0
        max_positive = anchor_scores["maximum_competitive_anchor"] > 0
        if contender_positive:
            sensitivity = "ROBUST_ACROSS_CONTENDER_AND_ELITE"
        elif elite_positive:
            sensitivity = "ELITE_CONTENDER_DEPENDENT"
        elif max_positive:
            sensitivity = "MAXIMUM_COMPETITIVE_WEIGHT_DEPENDENT"
        else:
            sensitivity = "NEGATIVE_ACROSS_GOVERNED_CONTENDER_RANGE"
        out.append({
            "description": row.get("description"),
            "target": (row.get("target") or {}).get("name"),
            "observed_team_improvement_score": row.get("team_improvement_score"),
            "counterparty_shared_decision_utility_score": row.get("counterparty_shared_decision_utility_score"),
            "current_primitive": round(c, 2),
            "future_primitive": round(f, 2),
            "actual_authorized_current_weight": round(actual_w, 6),
            "break_even_authorized_current_weight": round(breakeven, 6) if breakeven is not None else None,
            "anchor_scores": anchor_scores,
            "state_weight_sensitivity": sensitivity,
        })
    return {
        "schema_version": "FSFFL-Contender-State-Utility-Audit-1.0",
        "team_name": board.get("team_name"),
        "model_version": board.get("model_version"),
        "trade_case_count": len(out),
        "cases": out,
        "conclusion": {
            "coefficient_change_supported_by_this_audit": False,
            "reason": (
                "Real trade cases reveal useful state sensitivity but do not provide an "
                "independent outcome target capable of estimating objective weights. "
                "Changing expert-prior anchors to force intuitive trade answers would be unsupported."
            ),
            "diagnostic_use": (
                "Treat trades that are elite-contender-dependent as lower-confidence strategic "
                "recommendations and inspect their current-value primitive separately."
            ),
        },
    }

def render(doc):
    lines=[
        f"# Contender-State Utility Calibration Audit — {doc.get('team_name') or 'Team'}",
        "",
        "This audit measures sensitivity to the existing governed state-weight curve. It does **not** fit new coefficients.",
        "",
        "| Target | Observed utility | Break-even current weight | Contender anchor | Elite anchor | Sensitivity |",
        "|---|---:|---:|---:|---:|---|",
    ]
    seen=set()
    for x in doc.get("cases") or []:
        t=x.get("target") or "Unknown"
        if t in seen:
            continue
        seen.add(t)
        a=x["anchor_scores"]
        lines.append(
            f"| {t} | {float(x.get('observed_team_improvement_score') or 0):+,.0f} | "
            f"{float(x.get('break_even_authorized_current_weight') or 0):.3f} | "
            f"{float(a.get('contender_anchor') or 0):+,.0f} | "
            f"{float(a.get('elite_contender_anchor') or 0):+,.0f} | "
            f"{x.get('state_weight_sensitivity')} |"
        )
    lines += [
        "",
        "## Conclusion",
        "",
        "No objective-weight coefficient change is supported by these cases alone. They are sensitivity evidence, not an independent calibration target.",
        "",
        "A trade that remains positive at the contender anchor is not primarily explained by elite-contender weighting. A trade that flips between the contender and elite anchors is explicitly state-sensitive and should be treated as a lower-confidence strategic recommendation until objective weights can be validated against independent outcomes.",
        "",
    ]
    return "\n".join(lines)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--markdown")
    args=ap.parse_args()
    board=json.loads(Path(args.input).read_text(encoding="utf-8"))
    doc=audit(board)
    Path(args.output).write_text(json.dumps(doc,indent=2,sort_keys=True),encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render(doc),encoding="utf-8")
    print(json.dumps({
        "trade_case_count":doc["trade_case_count"],
        "coefficient_change_supported":doc["conclusion"]["coefficient_change_supported_by_this_audit"],
        "output":args.output,
    },indent=2))

if __name__=="__main__":
    main()
