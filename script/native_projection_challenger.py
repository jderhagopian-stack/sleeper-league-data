#!/usr/bin/env python3
"""Simple leakage-safe native projection challenger.

This module intentionally starts with a transparent position-specific ridge
regression rather than a complex ML model. It predicts underlying football
statistics, never league fantasy points. League scoring belongs downstream.

Rows represent information available before the target season. The model is
always evaluated on a later season than its training data. In addition to a
population-mean sanity baseline, any target named ``next_X`` is compared with
its natural prior-year persistence baseline ``lag1_X`` when that feature exists.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

DEFAULT_ALPHAS = (0.01, 0.1, 1.0, 10.0, 100.0)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 1.0
    m = _mean(xs)
    v = sum((x - m) ** 2 for x in xs) / len(xs)
    s = math.sqrt(v)
    return s if s > 1e-12 else 1.0


def _solve(a: List[List[float]], b: List[float]) -> List[float]:
    """Gaussian elimination with partial pivoting; adequate for small V1 models."""
    n = len(b)
    aug = [list(a[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            aug[pivot][col] = 1e-12
        aug[col], aug[pivot] = aug[pivot], aug[col]
        p = aug[col][col]
        aug[col] = [v / p for v in aug[col]]
        for r in range(n):
            if r == col:
                continue
            f = aug[r][col]
            if f:
                aug[r] = [aug[r][c] - f * aug[col][c] for c in range(n + 1)]
    return [aug[i][-1] for i in range(n)]


class RidgeModel:
    def __init__(self, alpha: float):
        self.alpha = float(alpha)
        self.means: List[float] = []
        self.stds: List[float] = []
        self.coef: List[float] = []

    def fit(self, X: List[List[float]], y: List[float]) -> "RidgeModel":
        if not X or not y or len(X) != len(y):
            raise ValueError("non-empty aligned X/y required")
        p = len(X[0])
        self.means = [_mean([row[j] for row in X]) for j in range(p)]
        self.stds = [_std([row[j] for row in X]) for j in range(p)]
        Z = [[1.0] + [(row[j] - self.means[j]) / self.stds[j] for j in range(p)] for row in X]
        q = p + 1
        xtx = [[0.0] * q for _ in range(q)]
        xty = [0.0] * q
        for row, target in zip(Z, y):
            for i in range(q):
                xty[i] += row[i] * target
                for j in range(q):
                    xtx[i][j] += row[i] * row[j]
        for j in range(1, q):
            xtx[j][j] += self.alpha
        self.coef = _solve(xtx, xty)
        return self

    def predict_one(self, x: List[float]) -> float:
        z = [1.0] + [(x[j] - self.means[j]) / self.stds[j] for j in range(len(x))]
        return sum(c * v for c, v in zip(self.coef, z))

    def predict(self, X: List[List[float]]) -> List[float]:
        return [self.predict_one(x) for x in X]


def mae(y: Sequence[float], pred: Sequence[float]) -> float:
    return _mean([abs(a - b) for a, b in zip(y, pred)])


def choose_alpha_temporally(
    rows: List[dict],
    features: List[str],
    target: str,
    alphas: Iterable[float] = DEFAULT_ALPHAS,
) -> Tuple[float, dict]:
    alphas = tuple(alphas)
    seasons = sorted({int(r["season"]) for r in rows})
    if len(seasons) < 3:
        return 1.0, {"method": "default_insufficient_seasons", "alpha": 1.0}
    validation_season = seasons[-1]
    train = [r for r in rows if int(r["season"]) < validation_season]
    valid = [r for r in rows if int(r["season"]) == validation_season]
    if not train or not valid:
        return 1.0, {"method": "default_empty_split", "alpha": 1.0}
    Xtr = [[float(r[f]) for f in features] for r in train]
    ytr = [float(r[target]) for r in train]
    Xv = [[float(r[f]) for f in features] for r in valid]
    yv = [float(r[target]) for r in valid]
    scores = {}
    for alpha in alphas:
        model = RidgeModel(alpha).fit(Xtr, ytr)
        scores[str(alpha)] = mae(yv, model.predict(Xv))
    best = min(alphas, key=lambda a: scores[str(a)])
    return float(best), {
        "method": "temporal_inner_validation",
        "validation_season": validation_season,
        "mae_by_alpha": scores,
        "alpha": float(best),
    }


def temporal_holdout(
    rows: List[dict],
    position: str,
    features: List[str],
    targets: List[str],
) -> dict:
    pos_rows = [r for r in rows if str(r["position"]).upper() == position.upper()]
    seasons = sorted({int(r["season"]) for r in pos_rows})
    if len(seasons) < 3:
        raise ValueError(f"{position}: need at least 3 seasons")
    holdout = seasons[-1]
    train = [r for r in pos_rows if int(r["season"]) < holdout]
    test = [r for r in pos_rows if int(r["season"]) == holdout]
    report = {
        "position": position.upper(),
        "train_seasons": sorted({int(r["season"]) for r in train}),
        "holdout_season": holdout,
        "train_n": len(train),
        "holdout_n": len(test),
        "targets": {},
    }
    for target in targets:
        alpha, alpha_diag = choose_alpha_temporally(train, features, target)
        Xtr = [[float(r[f]) for f in features] for r in train]
        ytr = [float(r[target]) for r in train]
        Xt = [[float(r[f]) for f in features] for r in test]
        yt = [float(r[target]) for r in test]
        model = RidgeModel(alpha).fit(Xtr, ytr)
        pred = model.predict(Xt)
        mean_baseline = [_mean(ytr)] * len(yt)
        model_mae = mae(yt, pred)
        mean_mae = mae(yt, mean_baseline)
        result = {
            "alpha": alpha,
            "alpha_selection": alpha_diag,
            "model_mae": model_mae,
            "mean_train_baseline_mae": mean_mae,
            "improvement_vs_mean_baseline_pct": (
                100.0 * (mean_mae - model_mae) / mean_mae if mean_mae > 1e-12 else 0.0
            ),
        }
        suffix = target[5:] if target.startswith("next_") else None
        persistence_feature = f"lag1_{suffix}" if suffix else None
        if persistence_feature and all(persistence_feature in r for r in test):
            persistence = [float(r[persistence_feature]) for r in test]
            persistence_mae = mae(yt, persistence)
            result.update({
                "persistence_feature": persistence_feature,
                "persistence_baseline_mae": persistence_mae,
                "improvement_vs_persistence_pct": (
                    100.0 * (persistence_mae - model_mae) / persistence_mae
                    if persistence_mae > 1e-12 else 0.0
                ),
                "beats_persistence": model_mae < persistence_mae,
            })
        report["targets"][target] = result
    return report


def load_csv(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def self_test() -> dict:
    rows = []
    for season in range(2018, 2025):
        for i in range(24):
            lag = 50 + i * 5 + (season - 2018) * 8
            context = (i % 4) * 10
            target = 0.55 * lag + 2.0 * context + 55 + ((i * 7 + season) % 7 - 3)
            rows.append({
                "season": season,
                "position": "WR",
                "lag1_receiving_yards": lag,
                "context": context,
                "next_receiving_yards": target,
            })
    report = temporal_holdout(
        rows, "WR", ["lag1_receiving_yards", "context"], ["next_receiving_yards"]
    )
    result = report["targets"]["next_receiving_yards"]
    assert report["holdout_season"] == 2024
    assert result["model_mae"] < result["mean_train_baseline_mae"]
    assert result["model_mae"] < result["persistence_baseline_mae"]
    assert result["beats_persistence"] is True
    assert result["alpha"] in DEFAULT_ALPHAS
    return {"status": "PASS", "report": report}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path)
    p.add_argument("--position")
    p.add_argument("--features", nargs="+")
    p.add_argument("--targets", nargs="+")
    p.add_argument("--output", type=Path)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return
    if not all([args.input, args.position, args.features, args.targets, args.output]):
        p.error("--input --position --features --targets --output are required")
    rows = load_csv(args.input)
    report = temporal_holdout(rows, args.position, args.features, args.targets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
