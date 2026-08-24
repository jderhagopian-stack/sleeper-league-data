#!/usr/bin/env python3
"""Fast continuous GM franchise-state objective weights.

Runtime contract:
- reads only small precomputed JSON artifacts;
- never reads historical transactions or runs a calibration search;
- never runs a simulator;
- falls back to the legacy GM 2.2 anchor logic if calibration is missing/invalid.

Offline calibration may replace data/gm/state_weight_calibration.json without
changing this runtime API.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CALIBRATION_PATH = DATA / "gm" / "state_weight_calibration.json"
SIM_CONTEXT_PATH = DATA / "gm" / "league" / "simulator_context.json"
WEIGHT_KEYS = ("current", "future", "liquidity", "resilience")

LEGACY_ANCHORS = [
    (0.00, {"current": 0.08, "future": 0.62, "liquidity": 0.20, "resilience": 0.10}),
    (0.35, {"current": 0.23, "future": 0.47, "liquidity": 0.15, "resilience": 0.15}),
    (0.55, {"current": 0.40, "future": 0.35, "liquidity": 0.10, "resilience": 0.15}),
    (0.78, {"current": 0.50, "future": 0.25, "liquidity": 0.10, "resilience": 0.15}),
    (1.00, {"current": 0.56, "future": 0.21, "liquidity": 0.08, "resilience": 0.15}),
]
LEGACY_THRESHOLDS = {"elite_contender": 0.78, "contender": 0.55, "retool": 0.35}


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def normalize(weights: Dict[str, float]) -> Dict[str, float]:
    vals = {k: max(sf(weights.get(k)), 0.0) for k in WEIGHT_KEYS}
    total = sum(vals.values()) or 1.0
    return {k: vals[k] / total for k in WEIGHT_KEYS}


@lru_cache(maxsize=1)
def load_calibration() -> Dict[str, Any]:
    try:
        raw = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
        anchors = raw.get("anchor_points") or []
        if len(anchors) < 2:
            raise ValueError("insufficient anchor points")
        for row in anchors:
            if not set(WEIGHT_KEYS).issubset((row.get("weights") or {}).keys()):
                raise ValueError("invalid weight keys")
        raw["runtime_source"] = "calibration_artifact"
        return raw
    except Exception:
        return {
            "model_version": "FSFFL-GM-State-Weights-Fallback",
            "status": "LEGACY_ANCHOR_FALLBACK",
            "runtime_source": "embedded_fallback",
            "classification_thresholds": LEGACY_THRESHOLDS,
            "anchor_points": [
                {"contender_score": score, "weights": weights}
                for score, weights in LEGACY_ANCHORS
            ],
            "adjustments": {
                "weak_dynasty": {"threshold": 0.30, "max_shift_current_to_future": 0.05},
                "championship_probability": {
                    "reference_probability": 0.08,
                    "slope": 0.25,
                    "max_shift_future_to_current": 0.04,
                    "max_shift_current_to_future": 0.03,
                },
            },
            "bounds": {
                "current": [0.05, 0.60], "future": [0.18, 0.68],
                "liquidity": [0.07, 0.24], "resilience": [0.08, 0.18],
            },
        }


@lru_cache(maxsize=1)
def simulator_index() -> Dict[str, Dict[str, Any]]:
    """Read the already-produced Simulator 1.0 context once; never simulate."""
    try:
        raw = json.loads(SIM_CONTEXT_PATH.read_text(encoding="utf-8"))
        return {str(x.get("user_id")): x for x in (raw.get("teams") or [])}
    except Exception:
        return {}


def classify(contender_score: float, thresholds: Dict[str, float] | None = None) -> str:
    t = thresholds or LEGACY_THRESHOLDS
    c = sf(contender_score, 0.5)
    if c >= sf(t.get("elite_contender"), 0.78):
        return "elite_contender"
    if c >= sf(t.get("contender"), 0.55):
        return "contender"
    if c >= sf(t.get("retool"), 0.35):
        return "retool"
    return "rebuild"


def interpolate(contender_score: float, anchor_points: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    c = clamp(sf(contender_score, 0.5), 0.0, 1.0)
    pts = sorted(
        [(sf(x.get("contender_score")), normalize(x.get("weights") or {})) for x in anchor_points],
        key=lambda x: x[0],
    )
    if not pts:
        pts = LEGACY_ANCHORS
    if c <= pts[0][0]:
        return dict(pts[0][1])
    if c >= pts[-1][0]:
        return dict(pts[-1][1])
    for (x0, w0), (x1, w1) in zip(pts, pts[1:]):
        if x0 <= c <= x1:
            t = 0.0 if x1 == x0 else (c - x0) / (x1 - x0)
            return normalize({k: w0[k] + t * (w1[k] - w0[k]) for k in WEIGHT_KEYS})
    return dict(pts[-1][1])


def _bounded_normalize(weights: Dict[str, float], bounds: Dict[str, Any]) -> Dict[str, float]:
    w = dict(weights)
    for _ in range(3):
        for k in WEIGHT_KEYS:
            lo, hi = (bounds.get(k) or [0.0, 1.0])[:2]
            w[k] = clamp(sf(w.get(k)), sf(lo), sf(hi, 1.0))
        w = normalize(w)
    return w


def resolve(
    team: Dict[str, Any],
    simulator_row: Dict[str, Any] | None = None,
    calibration: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve continuous objective weights in O(1) time from precomputed inputs."""
    cal = calibration or load_calibration()
    c = clamp(sf(team.get("contender_score"), 0.5), 0.0, 1.0)
    d = clamp(sf(team.get("dynasty_roster_score"), 0.5), 0.0, 1.0)
    uid = str(team.get("user_id") or team.get("owner_id") or "")
    sim = simulator_row if simulator_row is not None else simulator_index().get(uid, {})
    title = clamp(sf((sim or {}).get("championship_probability"), 0.08), 0.0, 1.0)

    w = interpolate(c, cal.get("anchor_points") or [])
    adj = cal.get("adjustments") or {}

    weak = adj.get("weak_dynasty") or {}
    threshold = sf(weak.get("threshold"), 0.30)
    max_shift = sf(weak.get("max_shift_current_to_future"), 0.05)
    dynasty_shift = 0.0
    if d < threshold and threshold > 0:
        dynasty_shift = max_shift * clamp((threshold - d) / threshold, 0.0, 1.0)
        dynasty_shift = min(dynasty_shift, max(w["current"] - 0.05, 0.0))
        w["current"] -= dynasty_shift
        w["future"] += dynasty_shift

    cp = adj.get("championship_probability") or {}
    ref = sf(cp.get("reference_probability"), 0.08)
    slope = sf(cp.get("slope"), 0.25)
    raw_title_shift = (title - ref) * slope
    if raw_title_shift >= 0:
        title_shift = min(raw_title_shift, sf(cp.get("max_shift_future_to_current"), 0.04), max(w["future"] - 0.18, 0.0))
    else:
        title_shift = max(raw_title_shift, -sf(cp.get("max_shift_current_to_future"), 0.03), -max(w["current"] - 0.05, 0.0))
    w["current"] += title_shift
    w["future"] -= title_shift

    w = _bounded_normalize(w, cal.get("bounds") or {})
    state = classify(c, cal.get("classification_thresholds") or LEGACY_THRESHOLDS)
    return {
        "state": state,
        "weights": {k: round(w[k], 6) for k in WEIGHT_KEYS},
        "inputs": {
            "contender_score": round(c, 6),
            "dynasty_roster_score": round(d, 6),
            "championship_probability": round(title, 6),
        },
        "adjustments": {
            "weak_dynasty_current_to_future": round(dynasty_shift, 6),
            "championship_future_to_current": round(title_shift, 6),
        },
        "calibration_model_version": cal.get("model_version"),
        "calibration_status": cal.get("status"),
        "runtime_source": cal.get("runtime_source", "calibration_artifact"),
    }


def weights_for_team(team: Dict[str, Any], simulator_row: Dict[str, Any] | None = None) -> Tuple[str, Dict[str, float]]:
    r = resolve(team, simulator_row=simulator_row)
    return r["state"], r["weights"]


if __name__ == "__main__":
    # Tiny runtime smoke demonstration; no historical data or simulations.
    for c in (0.10, 0.35, 0.54, 0.55, 0.77, 0.78, 0.92):
        print(c, resolve({"contender_score": c, "dynasty_roster_score": 0.5, "user_id": "demo"}))
