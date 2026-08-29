#!/usr/bin/env python3
"""Benchmark Stats Guy Fantasy as a replacement-capable dynasty market anchor.

Challenger-only: this script does not change production values. It compares the
current Stats Guy SF dynasty board with the frozen FantasyCalc snapshot by
Sleeper ID and performs time-ordered tests of whether recent market movement
adds forward information after accounting for the current player-value level.
No arbitrary replacement threshold is introduced.
"""
from __future__ import annotations

import json
import math
import statistics
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FC = ROOT / "data" / "market_values_fantasycalc.json"
OUT = ROOT / "data" / "audit" / "statsguy_market_anchor_benchmark.json"
API = "https://api.statsguyfantasy.com/api/v1"
DATES = [
    ("2026-02-01", "2026-03-01", "2026-04-01"),
    ("2026-03-01", "2026-04-01", "2026-05-01"),
    ("2026-04-01", "2026-05-01", "2026-06-01"),
    ("2026-05-01", "2026-06-01", "2026-07-01"),
]


def get(path: str, **params):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "FSFFL-market-source-benchmark/1.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def pearson(xs, ys):
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    dx, dy = [x-mx for x in xs], [y-my for y in ys]
    den = math.sqrt(sum(x*x for x in dx) * sum(y*y for y in dy))
    return sum(x*y for x, y in zip(dx, dy)) / den if den else None


def ranks(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j+1]] == vals[order[i]]:
            j += 1
        rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            out[order[k]] = rank
        i = j + 1
    return out


def spearman(xs, ys):
    return pearson(ranks(xs), ranks(ys)) if len(xs) >= 3 else None


def fit_linear(rows, features):
    p = len(features) + 1
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for row in rows:
        x = [1.0] + [float(row[f]) for f in features]
        y = float(row["future_change"])
        for i in range(p):
            xty[i] += x[i] * y
            for j in range(p):
                xtx[i][j] += x[i] * x[j]
    a = [xtx[i][:] + [xty[i]] for i in range(p)]
    for col in range(p):
        pivot = max(range(col, p), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return [0.0] * p
        a[col], a[pivot] = a[pivot], a[col]
        d = a[col][col]
        a[col] = [v / d for v in a[col]]
        for r in range(p):
            if r == col:
                continue
            factor = a[r][col]
            a[r] = [a[r][c] - factor * a[col][c] for c in range(p + 1)]
    return [a[i][-1] for i in range(p)]


def predict(row, features, coef):
    return coef[0] + sum(coef[i+1] * float(row[f]) for i, f in enumerate(features))


def errors(rows, features, coef):
    es = [float(r["future_change"]) - predict(r, features, coef) for r in rows]
    return {
        "n": len(es),
        "mae": statistics.mean(abs(e) for e in es) if es else None,
        "rmse": math.sqrt(statistics.mean(e*e for e in es)) if es else None,
    }


def residualize(values, control):
    rows = [{"future_change": float(v), "control": float(c)} for v, c in zip(values, control)]
    coef = fit_linear(rows, ["control"])
    return [float(r["future_change"]) - predict(r, ["control"], coef) for r in rows]


def fc_players():
    raw = json.loads(FC.read_text())
    rows = raw.get("dynasty", [])
    return {str(r.get("sleeper_id")): r for r in rows if r.get("position") in {"QB","RB","WR","TE"} and r.get("sleeper_id")}


def sg_current():
    p = get("/players")
    return {str(r["id"]): r for r in p.get("players", []) if r.get("position") in {"QB","RB","WR","TE"}}


def board(d):
    p = get("/rankings", format="sf_dynasty", date=d, limit=1000)
    return {str(r["id"]): r for r in p.get("rankings", []) if r.get("position") in {"QB","RB","WR","TE"}}


def overlap(a, b, n):
    aa = [k for k, _ in sorted(a.items(), key=lambda kv: kv[1])[:n]]
    bb = [k for k, _ in sorted(b.items(), key=lambda kv: kv[1])[:n]]
    return len(set(aa) & set(bb)) / n if n else None


def main():
    fc = fc_players()
    sg = sg_current()
    ids = sorted(set(fc) & set(sg))
    fc_rank = {i: float(fc[i].get("overall_rank") or 9999) for i in ids}
    sg_rank = {i: float((sg[i].get("rank") or {}).get("sf_dynasty") or 9999) for i in ids}
    fc_val = [float(fc[i]["value"]) for i in ids]
    sg_val = [float((sg[i].get("value") or {}).get("sf_dynasty") or 0) for i in ids]

    position_corr = {}
    for pos in ("QB","RB","WR","TE"):
        pids = [i for i in ids if fc[i].get("position") == pos and sg[i].get("position") == pos]
        position_corr[pos] = {"n": len(pids), "spearman_rank": spearman([fc_rank[i] for i in pids], [sg_rank[i] for i in pids])}

    history_rows = []
    pooled = []
    window_samples = []
    for past, now, future in DATES:
        bp, bn, bf = board(past), board(now), board(future)
        common = set(bp) & set(bn) & set(bf)
        sample = []
        for i in common:
            vp, vn, vf = float(bp[i]["value"]), float(bn[i]["value"]), float(bf[i]["value"])
            if vp <= 0 or vn <= 0:
                continue
            sample.append({"player_id": i, "prior_momentum": (vn-vp)/vp, "future_change": (vf-vn)/vn, "current_log_value": math.log(vn)})
        prior = [r["prior_momentum"] for r in sample]
        future_change = [r["future_change"] for r in sample]
        current = [r["current_log_value"] for r in sample]
        history_rows.append({
            "past": past, "now": now, "future": future, "n": len(sample),
            "momentum_future_change_pearson": pearson(prior, future_change),
            "partial_pearson_controlling_current_log_value": pearson(residualize(prior, current), residualize(future_change, current)),
        })
        window_samples.append(sample)
        pooled.extend(sample)

    pooled_prior = [r["prior_momentum"] for r in pooled]
    pooled_future = [r["future_change"] for r in pooled]
    pooled_current = [r["current_log_value"] for r in pooled]
    pooled_partial = pearson(residualize(pooled_prior, pooled_current), residualize(pooled_future, pooled_current))

    train = [r for sample in window_samples[:-1] for r in sample]
    holdout = list(window_samples[-1])
    baseline_features = ["current_log_value"]
    momentum_features = ["current_log_value", "prior_momentum"]
    base_err = errors(holdout, baseline_features, fit_linear(train, baseline_features))
    mom_err = errors(holdout, momentum_features, fit_linear(train, momentum_features))
    holdout_compare = {
        "train_windows": [list(x) for x in DATES[:-1]],
        "holdout_window": list(DATES[-1]),
        "baseline": base_err,
        "baseline_plus_momentum": mom_err,
        "mae_improvement": base_err["mae"] - mom_err["mae"],
        "rmse_improvement": base_err["rmse"] - mom_err["rmse"],
        "momentum_improves_both_holdout_metrics": bool(mom_err["mae"] < base_err["mae"] and mom_err["rmse"] < base_err["rmse"]),
    }

    payload = {
        "model_version": "FSFFL-StatsGuy-Market-Anchor-Benchmark-1.1",
        "generated": date.today().isoformat(),
        "projection_behavior_changed": False,
        "production_model_behavior_changed": False,
        "source_replacement_authorized": False,
        "reason_no_automatic_replacement": "Current-provider comparability is measured here; player-market production promotion remains a separate downstream decision test.",
        "current_board": {
            "matched_players": len(ids), "fantasycalc_players": len(fc), "statsguy_players": len(sg),
            "coverage_of_fantasycalc": len(ids)/len(fc) if fc else None,
            "spearman_overall_rank": spearman([fc_rank[i] for i in ids], [sg_rank[i] for i in ids]),
            "spearman_raw_value": spearman(fc_val, sg_val),
            "top_25_overlap": overlap(fc_rank, sg_rank, 25), "top_50_overlap": overlap(fc_rank, sg_rank, 50), "top_100_overlap": overlap(fc_rank, sg_rank, 100),
            "position_rank_correlations": position_corr,
        },
        "historical_momentum_test": {
            "design": "Time-ordered 30-day momentum test, including partial correlation controlling current log value and a final-window holdout comparison of current-value-only vs current-value-plus-momentum.",
            "windows": history_rows,
            "pooled_n": len(pooled),
            "pooled_momentum_future_change_pearson": pearson(pooled_prior, pooled_future),
            "pooled_partial_pearson_controlling_current_log_value": pooled_partial,
            "temporal_holdout": holdout_compare,
            "note": "Momentum is eligible for reconsideration only if it adds repeatable out-of-sample information after current value is accounted for; this benchmark does not tune a production coefficient.",
        },
        "governance": {
            "fantasycalc_can_become_optional_if_challenger_is_sufficiently_complete_and_decision_robust": True,
            "raw_provider_scales_need_not_match": True,
            "ranking_and_downstream_decision_robustness_more_important_than_raw_scale": True,
            "momentum_remains_zero_weight_until_incremental_temporal_value_is_demonstrated": True,
            "no_new_coefficient_introduced": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
