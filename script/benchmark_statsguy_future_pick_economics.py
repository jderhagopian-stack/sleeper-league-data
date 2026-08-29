#!/usr/bin/env python3
"""Benchmark Stats Guy Fantasy future-pick economics against the current anchor.

This is a challenger audit only. It deliberately does not alter production values or
projection logic. The comparison is scale-free where possible because provider value
scales differ; the economically relevant objects here are early/mid/late shape and
cross-year time-value ratios.
"""
from __future__ import annotations

import json
import math
import statistics
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FC_PATH = ROOT / "data" / "market_values_fantasycalc.json"
OUT = ROOT / "data" / "audit" / "statsguy_future_pick_benchmark.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

API = "https://api.statsguyfantasy.com/api/v1"
YEARS = (2027, 2028, 2029)
ROUNDS = (1, 2, 3)
HISTORICAL_PROBES = (
    "2025-09-15",
    "2025-12-15",
    "2026-03-15",
    "2026-06-15",
)


def _request_json(method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {"User-Agent": "FSFFL-governance-audit/1.1"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request_json("GET", path, params=params)


def _post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    return _request_json("POST", path, body=body)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _extract_fc_rows() -> list[dict[str, Any]]:
    raw = json.loads(FC_PATH.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    for key in ("values", "rows", "data", "players"):
        if isinstance(raw.get(key), list):
            return raw[key]
    return []


def _fc_pick_map() -> dict[tuple[int, str, int], float]:
    """Parse explicit FantasyCalc future-pick rows; generic rows never overwrite explicit mid."""
    parsed: dict[tuple[int, str, int], tuple[float, bool]] = {}
    for row in _extract_fc_rows():
        name = str(row.get("player") or row.get("name") or row.get("label") or "").strip()
        lower = name.lower()
        if not name or ("1st" not in lower and "2nd" not in lower and "3rd" not in lower):
            continue
        year = next((y for y in YEARS if str(y) in name), None)
        if year is None:
            continue
        rnd = 1 if "1st" in lower else 2 if "2nd" in lower else 3 if "3rd" in lower else None
        if rnd is None:
            continue
        explicit = any(t in lower for t in ("early", "mid", "late"))
        tier = "early" if "early" in lower else "late" if "late" in lower else "mid"
        value = _num(row.get("value") or row.get("sf_value") or row.get("superflex") or row.get("fantasycalc_value"))
        if value is None or value <= 0:
            continue
        key = (year, tier, rnd)
        prev = parsed.get(key)
        if prev is None or (explicit and not prev[1]):
            parsed[key] = (value, explicit)
    return {k: v for k, (v, _) in parsed.items()}


def _statsguy_pick_map(payloads: dict[int, dict[str, Any]]) -> tuple[dict[tuple[int, str, int], float], dict[tuple[int, int], float]]:
    """Parse documented Stats Guy pick cards.

    Current /picks rows use structured IDs such as pick:2027:1:early and place
    Superflex dynasty value under value.sf_dynasty. Variant is preferred when present,
    with the ID used as a compatibility fallback.
    """
    tiers: dict[tuple[int, str, int], float] = {}
    bases: dict[tuple[int, int], float] = {}
    for requested_year, payload in payloads.items():
        for row in payload.get("picks", []):
            rid = str(row.get("id") or "")
            parts = rid.split(":") if rid.startswith("pick:") else []
            year = int(row.get("year", 0) or 0)
            rnd = int(row.get("round", 0) or 0)
            if (not year or not rnd) and len(parts) >= 3:
                try:
                    year = year or int(parts[1])
                    rnd = rnd or int(float(parts[2]))
                except (TypeError, ValueError):
                    pass
            if year != requested_year or rnd not in ROUNDS:
                continue
            value_obj = row.get("value", {})
            value = _num(value_obj.get("sf_dynasty") if isinstance(value_obj, dict) else None)
            if value is None or value <= 0:
                continue
            variant = str(row.get("variant") or "").lower()
            if not variant and len(parts) >= 4 and parts[3] in {"early", "mid", "late"}:
                variant = parts[3]
            if variant in {"early", "mid", "late"}:
                tiers[(year, variant, rnd)] = value
            else:
                # Round-only generic pick IDs are pick:YYYY:R; slot-specific IDs contain a decimal.
                generic_id = len(parts) == 3 and "." not in parts[2]
                if generic_id or row.get("slot") in (None, ""):
                    bases[(year, rnd)] = value
    return tiers, bases


def _tier_shape(values: dict[tuple[int, str, int], float]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for y in YEARS:
        for r in ROUNDS:
            mid = values.get((y, "mid", r))
            early = values.get((y, "early", r))
            late = values.get((y, "late", r))
            if not mid or not early or not late:
                continue
            out[f"{y}_r{r}"] = {
                "early_to_mid": early / mid,
                "late_to_mid": late / mid,
            }
    return out


def _time_curve_from_bases(bases: dict[tuple[int, int], float], tiers: dict[tuple[int, str, int], float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in ROUNDS:
        vals = {y: bases.get((y, r)) or tiers.get((y, "mid", r)) for y in YEARS}
        for a, b in zip(YEARS, YEARS[1:]):
            if vals.get(a) and vals.get(b):
                out[f"r{r}_{b}_over_{a}"] = float(vals[b]) / float(vals[a])
    return out


def _pairwise_deviation(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    diffs = []
    rows = {}
    for key in sorted(set(a) & set(b)):
        av = _num(a[key])
        bv = _num(b[key])
        if av is None or bv is None or av == 0:
            continue
        rel = abs(bv - av) / abs(av)
        diffs.append(rel)
        rows[key] = {"current": av, "challenger": bv, "absolute_relative_difference": rel}
    return {
        "n": len(diffs),
        "mean_absolute_relative_difference": statistics.mean(diffs) if diffs else None,
        "median_absolute_relative_difference": statistics.median(diffs) if diffs else None,
        "max_absolute_relative_difference": max(diffs) if diffs else None,
        "details": rows,
    }


def _flatten_shape(shape: dict[str, dict[str, float]]) -> dict[str, float]:
    return {f"{k}:{metric}": value for k, row in shape.items() for metric, value in row.items()}


def _historical_eval_snapshot(d: str) -> dict[str, Any]:
    """Request historical pick values through the documented retro-trade endpoint."""
    ids = []
    for y in YEARS:
        for r in ROUNDS:
            ids.extend([f"pick:{y}:{r}:early", f"pick:{y}:{r}:mid", f"pick:{y}:{r}:late", f"pick:{y}:{r}"])
    payload = _post_json("/trades/evaluate", {
        "format": "sf_dynasty",
        "date": d,
        "sideA": ids[:18],
        "sideB": ids[18:],
    })
    assets = list(payload.get("sideA", {}).get("assets", [])) + list(payload.get("sideB", {}).get("assets", []))
    tiers: dict[tuple[int, str, int], float] = {}
    bases: dict[tuple[int, int], float] = {}
    as_of_dates: set[str] = set()
    found = 0
    for asset in assets:
        if not asset.get("found"):
            continue
        found += 1
        aid = str(asset.get("id") or "")
        value = _num(asset.get("value"))
        stamp = asset.get("asOf") or payload.get("asOf")
        if stamp:
            as_of_dates.add(str(stamp)[:10])
        if value is None or value <= 0 or not aid.startswith("pick:"):
            continue
        parts = aid.split(":")
        if len(parts) < 3:
            continue
        try:
            y = int(parts[1])
            r = int(float(parts[2]))
        except (TypeError, ValueError):
            continue
        if y not in YEARS or r not in ROUNDS:
            continue
        if len(parts) >= 4 and parts[3] in {"early", "mid", "late"}:
            tiers[(y, parts[3], r)] = value
        elif "." not in parts[2]:
            bases[(y, r)] = value
    return {
        "requested": d,
        "found_assets": found,
        "as_of_dates": sorted(as_of_dates),
        "tier_shape": _tier_shape(tiers),
        "time_curve": _time_curve_from_bases(bases, tiers),
    }


def _historical_probe() -> dict[str, Any]:
    results = []
    for d in HISTORICAL_PROBES:
        try:
            snap = _historical_eval_snapshot(d)
            honored = bool(snap["found_assets"] and snap["as_of_dates"] and max(snap["as_of_dates"]) <= d)
            snap["date_parameter_honored"] = honored
            results.append(snap)
        except Exception as exc:
            results.append({"requested": d, "error": f"{type(exc).__name__}: {exc}", "date_parameter_honored": False})
    usable = [r for r in results if r.get("date_parameter_honored") and (r.get("tier_shape") or r.get("time_curve"))]
    return {
        "endpoint": "/trades/evaluate",
        "probes": results,
        "historical_pick_snapshots_confirmed": len(usable) >= 2,
        "authoritative_temporal_stability_claim_allowed": len(usable) >= 2,
    }


def main() -> int:
    fc = _fc_pick_map()
    statsguy_payloads = {y: _get_json("/picks", {"year": y}) for y in YEARS}
    sg_tiers, sg_bases = _statsguy_pick_map(statsguy_payloads)

    fc_shape = _tier_shape(fc)
    sg_shape = _tier_shape(sg_tiers)
    fc_time = _time_curve_from_bases({}, fc)
    sg_time = _time_curve_from_bases(sg_bases, sg_tiers)

    shape_comparison = _pairwise_deviation(_flatten_shape(fc_shape), _flatten_shape(sg_shape))
    time_comparison = _pairwise_deviation(fc_time, sg_time)
    history = _historical_probe()

    payload = {
        "model_version": "FSFFL-StatsGuy-Future-Pick-Benchmark-1.1",
        "generated_utc": date.today().isoformat(),
        "purpose": "Commercially permissible challenger benchmark; not a production replacement authorization.",
        "projection_behavior_changed": False,
        "production_model_behavior_changed": False,
        "source": {
            "name": "Stats Guy Fantasy",
            "endpoint": "/api/v1/picks plus /api/v1/trades/evaluate for dated history",
            "format": "sf_dynasty",
            "commercial_use": "allowed_with_conditions_per_provider_terms",
            "provider_method": "trade-derived values; future variants use market-informed pick shape",
        },
        "current_anchor": "FantasyCalc snapshot already present in repository",
        "comparison_design": {
            "scale_free": True,
            "reason": "Provider raw value scales differ, so compare early/mid/late ratios and adjacent-year ratios rather than raw points.",
            "automatic_replacement_threshold": None,
            "reason_no_threshold": "No hand-set accuracy cutoff is introduced merely to force a source replacement.",
        },
        "fantasycalc_tier_shape": fc_shape,
        "statsguy_tier_shape": sg_shape,
        "tier_shape_comparison": shape_comparison,
        "fantasycalc_time_curve": fc_time,
        "statsguy_time_curve": sg_time,
        "time_curve_comparison": time_comparison,
        "historical_pick_snapshot_probe": history,
        "evidence_assessment": {
            "current_market_challenger_observed": bool(sg_shape or sg_time),
            "historical_out_of_sample_pick_curve_validation_available": history["authoritative_temporal_stability_claim_allowed"],
            "replacement_authorized": False,
            "replacement_reason": "Benchmark evidence must be reviewed; current-market agreement alone is not historical/out-of-sample validation.",
        },
        "next_step_policy": [
            "If current market shapes materially agree, Stats Guy can serve as a commercially permissible external anchor challenger.",
            "If they materially disagree, investigate methodology and realized league outcomes before choosing either curve.",
            "Do not change team-to-early/mid/late probability formulas from this market benchmark.",
            "Do not infer historical validation unless dated pick snapshots are actually confirmed by the API.",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "tier_shape": shape_comparison,
        "time_curve": time_comparison,
        "historical": history,
        "replacement_authorized": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
