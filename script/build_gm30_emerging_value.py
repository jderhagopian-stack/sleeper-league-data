#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Historically Calibrated Emerging Value Intelligence

Decision sequence:
  historical breakout profile -> current catalyst -> market lag

Historical profile thresholds and feature weights come from:
  data/gm/breakout_calibration.json

Current catalysts come from:
  data/football_intelligence_signals.json

Output:
  data/gm/emerging_value.json
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data")
OUT = DATA / "gm"
MODEL = "FSFFL-GM-3.0-Emerging-Value-v4-Historical-Calibrated"
POSITIONS = {"QB", "RB", "WR", "TE"}
PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "player_stats/player_stats.csv.gz"
)

def load(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default

def num(x, default=None):
    try:
        if x is None or isinstance(x, bool):
            return default
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default

def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

def first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d.get(k)
    return default

def norm_name(x):
    x = str(x or "").lower()
    x = re.sub(r"[^a-z0-9 ]+", "", x)
    return re.sub(r"\s+", " ", x).strip()

def roster_owners():
    out = {}
    for r in load(DATA / "rosters.json", []) or []:
        rid = r.get("roster_id")
        for pid in (r.get("players") or []):
            out[str(pid)] = rid
    return out

def values_by_player():
    payload = load(DATA / "fsffl_asset_values.json", {}) or {}
    rows = payload.get("players", []) if isinstance(payload, dict) else []
    return {str(x.get("player_id")): x for x in rows if x.get("player_id") is not None}

def intelligence():
    x = load(DATA / "football_intelligence_signals.json", {}) or {}
    return (
        x,
        x.get("usage") or {},
        x.get("snaps") or {},
        x.get("prior_snaps") or {},
        x.get("preseason_usage") or {},
        x.get("manual_intelligence") or {},
    )

def player_universe():
    raw = load(DATA / "players.json", {}) or {}
    if isinstance(raw, list):
        raw = {str(x.get("player_id")): x for x in raw if isinstance(x, dict)}
    return raw

def age_curve(pos, age):
    if age is None:
        return None
    ideal = {"QB": 25.0, "RB": 22.5, "WR": 23.5, "TE": 24.5}.get(pos, 24)
    fade = {"QB": 37.0, "RB": 29.0, "WR": 31.0, "TE": 32.0}.get(pos, 31)
    if age <= ideal:
        return 1.0
    return clamp(1 - (age - ideal) / (fade - ideal))

def pedigree(p):
    pick = num(first(p, "draft_pick", "draft_pick_number"))
    rnd = num(first(p, "draft_round"))
    if pick is not None:
        return clamp(1 - (pick - 1) / 256)
    if rnd is not None:
        return clamp(1 - (rnd - 1) / 7)
    return None

def market_value(v):
    return num(first(v, "market_value", "value", "fsffl_value", "dynasty_value", "ktc_value"))

def normalize_market(rows):
    pairs = [(pid, market_value(v)) for pid, v in rows.items()]
    pairs = [(pid, val) for pid, val in pairs if val is not None and val >= 0]
    if not pairs:
        return {}
    ordered = sorted(pairs, key=lambda x: x[1])
    n = max(len(ordered) - 1, 1)
    return {pid: i / n for i, (pid, _) in enumerate(ordered)}

def usage_features(u, s, prior, preseason, phase):
    def collect(d, keys):
        vals = []
        for key in keys:
            x = num(first(d, key))
            if x is None:
                continue
            if x > 1:
                x /= 100
            vals.append(clamp(x))
        return vals

    current = collect(s, ("snap_share", "offense_snap_pct", "offensive_snap_pct")) + \
              collect(u, ("route_participation", "route_share", "routes_pct",
                          "target_share", "tgt_share", "opportunity_share",
                          "touch_share", "carry_share"))
    prior_vals = collect(prior, ("offense_snap_pct", "snap_share"))
    preseason_vals = collect(preseason, ("snap_share", "offense_snap_pct",
                                         "route_participation", "target_share",
                                         "touch_share", "carry_share"))

    if phase in {"TRAINING_CAMP", "PRESEASON", "OFFSEASON", "POSTSEASON"}:
        weighted = []
        if preseason_vals:
            weighted.append((sum(preseason_vals) / len(preseason_vals), 0.55))
        if prior_vals:
            weighted.append((sum(prior_vals) / len(prior_vals), 0.45))
        if not weighted:
            return None, 0
        denom = sum(w for _, w in weighted)
        return sum(v * w for v, w in weighted) / denom, len(preseason_vals) + len(prior_vals)

    vals = current or prior_vals
    return (sum(vals) / len(vals) if vals else None), len(vals)

def manual_features(m):
    if not isinstance(m, dict):
        return None, [], 0
    score = 0.5
    evidence = []
    n = 0
    signals = {
        "depth_chart_rise": 0.18,
        "starter_reps": 0.16,
        "camp_buzz": 0.10,
        "preseason_role": 0.12,
        "injury_opportunity": 0.16,
        "coach_praise": 0.07,
        "depth_chart_fall": -0.18,
        "injury_concern": -0.14,
        "role_loss": -0.20,
    }
    for k, w in signals.items():
        if m.get(k):
            score += w
            evidence.append(k)
            n += 1
    return clamp(score), evidence, n

def fetch_prior_ppg(active_season):
    """Fetch prior completed regular-season PPG by normalized player name."""
    try:
        req = urllib.request.Request(
            PLAYER_STATS_URL,
            headers={"User-Agent": "FSFFL-GM30-Emerging/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = gzip.decompress(r.read())
        rows = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    except Exception:
        return {}, "PRIOR_PPG_FETCH_FAILED"

    prior = active_season - 1
    agg = {}
    games = {}
    for r in rows:
        try:
            season = int(float(r.get("season") or 0))
        except Exception:
            continue
        if season != prior:
            continue
        if str(r.get("season_type") or r.get("game_type") or "REG").upper() not in {"REG", "REGULAR"}:
            continue
        pos = str(r.get("position") or r.get("position_group") or "").upper()
        if pos not in POSITIONS:
            continue
        name = norm_name(r.get("player_display_name") or r.get("player_name") or r.get("name"))
        if not name:
            continue
        fp = (
            0.04 * num(r.get("passing_yards"), 0)
            + 4.0 * num(r.get("passing_tds"), 0)
            - 1.0 * num(r.get("interceptions"), 0)
            + 0.10 * num(r.get("rushing_yards"), 0)
            + 6.0 * num(r.get("rushing_tds"), 0)
            + 0.50 * num(r.get("receptions"), 0)
            + 0.10 * num(r.get("receiving_yards"), 0)
            + 6.0 * num(r.get("receiving_tds"), 0)
            - 1.0 * num(r.get("rushing_fumbles_lost") or r.get("fumbles_lost"), 0)
        )
        agg[name] = agg.get(name, 0.0) + fp
        game_key = str(r.get("game_id") or r.get("week") or "")
        games.setdefault(name, set()).add(game_key or f"row-{len(games.get(name, set()))}")
    return {n: agg[n] / max(len(games.get(n, set())), 1) for n in agg}, None

def bin_age(age):
    if age is None:
        return "UNKNOWN"
    if age <= 22:
        return "AGE_22_OR_YOUNGER"
    if age <= 24:
        return "AGE_23_24"
    if age <= 26:
        return "AGE_25_26"
    if age <= 28:
        return "AGE_27_28"
    return "AGE_29_PLUS"

def bin_draft(round_):
    if round_ is None:
        return "DRAFT_UNKNOWN_UDFA"
    if int(round_) == 1:
        return "ROUND_1"
    if int(round_) == 2:
        return "ROUND_2"
    if int(round_) == 3:
        return "ROUND_3"
    return "ROUND_4_PLUS"

def bin_snap(x):
    if x is None:
        return "SNAP_UNKNOWN"
    if x < 0.20:
        return "SNAP_LT_20"
    if x < 0.45:
        return "SNAP_20_44"
    if x < 0.70:
        return "SNAP_45_69"
    return "SNAP_70_PLUS"

def breakout_thresholds(pos):
    return {
        "QB": {"prior_ppg_ceiling": 16.0},
        "RB": {"prior_ppg_ceiling": 9.5},
        "WR": {"prior_ppg_ceiling": 9.5},
        "TE": {"prior_ppg_ceiling": 6.5},
    }[pos]

def bin_ppg(pos, ppg):
    if ppg is None:
        return None
    ceiling = breakout_thresholds(pos)["prior_ppg_ceiling"]
    if ppg < ceiling * 0.45:
        return "PPG_LOW"
    if ppg < ceiling * 0.80:
        return "PPG_BELOW_AVG"
    if ppg < ceiling:
        return "PPG_NEAR_BREAKOUT"
    return "PPG_ALREADY_HIGH"

def bin_experience(exp):
    if exp is None:
        return None
    if exp <= 0:
        return "EXP_ROOKIE"
    if exp == 1:
        return "EXP_YEAR_2"
    if exp == 2:
        return "EXP_YEAR_3"
    if exp <= 4:
        return "EXP_YEAR_4_5"
    return "EXP_YEAR_6_PLUS"

def historical_profile(calibration, pos, age, exp, draft_round, prior_snap, prior_ppg):
    rookie = exp is not None and exp <= 0
    cohort_key = "rookie_year" if rookie else "veteran_next_year"
    cohort = calibration.get(cohort_key) or {}
    pos_model = (cohort.get("by_position") or {}).get(pos) or {}
    weights = pos_model.get("learned_weights") or {}
    threshold = (((cohort.get("thresholds") or {}).get(pos) or {}).get("recommended") or {}).get("score_cutoff")

    features = {
        "age": bin_age(age),
        "draft_capital": bin_draft(draft_round),
        "experience": bin_experience(exp),
    }
    if not rookie:
        features["prior_snap"] = bin_snap(prior_snap)
        ppg_bin = bin_ppg(pos, prior_ppg)
        if ppg_bin is not None:
            features["prior_ppg"] = ppg_bin

    score = 0.0
    used = 0
    contributions = {}
    for feature, value in features.items():
        if value is None:
            continue
        w = (weights.get(feature) or {}).get(value)
        if w is None:
            continue
        score += float(w)
        used += 1
        contributions[feature] = {"value": value, "weight": float(w)}

    expected = 3 if rookie else 5
    coverage = used / expected
    clears = threshold is not None and score >= float(threshold)
    return {
        "cohort": "ROOKIE_YEAR" if rookie else "VETERAN_NEXT_YEAR",
        "score": round(score, 3),
        "cutoff": float(threshold) if threshold is not None else None,
        "clears_cutoff": bool(clears),
        "feature_coverage": round(coverage, 2),
        "features_used": used,
        "contributions": contributions,
        "base_breakout_rate": pos_model.get("base_breakout_rate"),
    }

def catalyst_profile(m, pre):
    evidence = m.get("evidence") if isinstance(m, dict) else []
    evidence = evidence if isinstance(evidence, list) else []
    strengths = [num(x.get("strength"), 0) for x in evidence if isinstance(x, dict)]
    max_strength = max(strengths) if strengths else 0.0
    strong_count = sum(1 for x in strengths if x >= 0.75)
    independent_sources = len({
        str(x.get("source"))
        for x in evidence
        if isinstance(x, dict) and x.get("source")
    })
    structured_preseason = bool(pre)

    # Generic public-news keyword matches are corroboration, not a strong catalyst.
    strong = structured_preseason or max_strength >= 0.75
    corroborated = structured_preseason or strong_count >= 2 or independent_sources >= 2

    return {
        "present": bool(pre) or bool(evidence),
        "structured_preseason_usage": structured_preseason,
        "evidence_count": len(evidence),
        "strong_evidence_count": strong_count,
        "independent_sources": independent_sources,
        "max_strength": round(max_strength, 2),
        "strong": strong,
        "corroborated": corroborated,
    }

def classify(row):
    tags = []
    direction = "MONITOR"
    hist = row["historical_breakout_profile"]
    catalyst = row["current_catalyst_profile"]
    mkt = row["market_score"]
    latent = row["latent_value_score"] / 100.0
    gap = row["market_mispricing_score"]
    gap = (gap / 100.0) if gap is not None else None
    rostered = row["fsffl_rostered"]

    hist_ok = bool(hist.get("clears_cutoff"))
    hist_coverage = float(hist.get("feature_coverage") or 0)
    strong_now = bool(catalyst.get("strong"))
    corroborated_now = bool(catalyst.get("corroborated"))

    # Historical profile alone creates WATCHLIST status, never a breakout alert.
    if hist_ok:
        tags.append("HISTORICAL_BREAKOUT_PROFILE")

    # Structural market mispricing: require history or broad football evidence.
    if gap is not None and gap >= 0.25 and latent >= 0.62 and (hist_ok or row["usage_score"] is not None):
        tags.append("HIDDEN_GEM")

    # Empirically calibrated breakout gate:
    # 1) clear position/cohort historical cutoff
    # 2) meaningful current catalyst
    # 3) market is not already fully pricing it
    if hist_ok and strong_now and gap is not None and gap >= 0.08:
        tags.append("BREAKOUT_CANDIDATE")

    # Highest urgency requires corroboration, not one signal.
    if hist_ok and corroborated_now and gap is not None and gap >= 0.15:
        tags.append("HIGH_PRIORITY_BREAKOUT_ALERT")

    if not rostered and hist_ok and strong_now:
        tags.append("WAIVER_TARGET")

    if rostered and gap is not None and gap >= 0.18 and hist_ok:
        tags.append("BUY_LOW")

    if hist_ok and hist_coverage >= 0.67 and mkt is not None and mkt <= 0.40:
        tags.append("DYNASTY_STASH")

    # Role inflection requires a strong current signal; history is useful but not mandatory.
    if strong_now and row["manual_score"] is not None and row["manual_score"] >= 0.68:
        tags.append("ROLE_INFLECTION")

    # Negative market-risk tags remain conservative.
    if mkt is not None and mkt >= 0.72 and latent <= 0.50 and mkt - latent >= 0.18:
        tags.append("FRAGILE_VALUE")
    if mkt is not None and mkt >= 0.60 and latent <= 0.42:
        tags.append("VALUE_TRAP_RISK")

    sell = {"FRAGILE_VALUE", "VALUE_TRAP_RISK"}
    if any(x in tags for x in sell):
        direction = "SELL_OR_AVOID"
    elif "WAIVER_TARGET" in tags:
        direction = "ADD"
    elif "HIGH_PRIORITY_BREAKOUT_ALERT" in tags:
        direction = "PRIORITY_ACQUIRE"
    elif any(x in tags for x in {"BREAKOUT_CANDIDATE", "HIDDEN_GEM", "BUY_LOW", "DYNASTY_STASH", "ROLE_INFLECTION"}):
        direction = "ACQUIRE"
    elif "HISTORICAL_BREAKOUT_PROFILE" in tags:
        direction = "WATCHLIST"
    return tags, direction

def main():
    players = player_universe()
    owners = roster_owners()
    vals = values_by_player()
    market_norm = normalize_market(vals)
    fi, usage, snaps, prior_snaps, preseason_usage, manual = intelligence()
    phase = str(fi.get("season_phase") or "UNKNOWN")
    active_season = int(fi.get("active_season") or (load(DATA / "league.json", {}) or {}).get("season"))
    calibration = load(OUT / "breakout_calibration.json", {}) or {}
    prior_ppg, prior_ppg_warning = fetch_prior_ppg(active_season)
    rows = []

    for pid, p in players.items():
        if not isinstance(p, dict):
            continue
        pos = str(first(p, "position", default="")).upper()
        if pos not in POSITIONS or p.get("active") is False:
            continue
        name = first(p, "full_name", "name")
        if not name or name == pid:
            continue

        age = num(p.get("age"))
        exp = num(first(p, "years_exp", "experience"))
        v = vals.get(str(pid), {})
        u = usage.get(str(pid), {}) if isinstance(usage, dict) else {}
        s = snaps.get(str(pid), {}) if isinstance(snaps, dict) else {}
        prior = prior_snaps.get(str(pid), {}) if isinstance(prior_snaps, dict) else {}
        pre = preseason_usage.get(str(pid), {}) if isinstance(preseason_usage, dict) else {}
        m = manual.get(str(pid), {}) if isinstance(manual, dict) else {}

        uscore, usage_n = usage_features(u, s, prior, pre, phase)
        mscore, manual_evidence, manual_n = manual_features(m)
        if manual_n == 0:
            mscore = None

        prior_snap = num(first(prior, "offense_snap_pct", "snap_share"))
        ppg = prior_ppg.get(norm_name(name))
        hist = historical_profile(
            calibration,
            pos,
            age,
            exp,
            num(p.get("draft_round")),
            prior_snap,
            ppg,
        )
        catalyst = catalyst_profile(m, pre)

        # Keep latent score for market-mispricing context, but breakout classification
        # no longer comes from this hand-built score.
        structural = [x for x in (age_curve(pos, age), pedigree(p)) if x is not None]
        football = [x for x in (uscore, mscore) if x is not None]
        pieces = []
        if structural:
            pieces.append((sum(structural) / len(structural), 0.30))
        if football:
            pieces.append((sum(football) / len(football), 0.45))
        hist_signal = clamp((hist["score"] - (hist["cutoff"] or 0)) / 3 + 0.5) if hist["cutoff"] is not None else 0.5
        pieces.append((hist_signal, 0.25))
        denom = sum(w for _, w in pieces)
        latent = sum(x * w for x, w in pieces) / denom if denom else 0.5

        mkt = market_norm.get(str(pid))
        gap = latent - mkt if mkt is not None else None

        row = {
            "player_id": str(pid),
            "name": name,
            "position": pos,
            "nfl_team": first(p, "team", default=first(v, "nfl_team")),
            "age": age,
            "years_exp": exp,
            "draft_round": num(p.get("draft_round")),
            "draft_pick": num(p.get("draft_pick")),
            "fsffl_rostered": str(pid) in owners,
            "fsffl_roster_id": owners.get(str(pid)),
            "market_value": market_value(v),
            "market_score": mkt,
            "usage_score": uscore,
            "manual_score": mscore,
            "manual_evidence": manual_evidence,
            "season_phase": phase,
            "prior_snap_evidence": prior if prior else None,
            "prior_half_ppr_ppg": round(ppg, 3) if ppg is not None else None,
            "preseason_usage_evidence": pre if pre else None,
            "historical_breakout_profile": hist,
            "current_catalyst_profile": catalyst,
            "latent_value_score": round(latent * 100, 1),
            "market_mispricing_score": round(gap * 100, 1) if gap is not None else None,
        }

        tags, direction = classify(row)
        row["signals"] = tags
        row["direction"] = direction

        # Confidence emphasizes historical feature completeness + current-source quality.
        hist_cov = float(hist.get("feature_coverage") or 0)
        catalyst_quality = 0.0
        if catalyst["present"]:
            catalyst_quality = min(
                1.0,
                0.45 * catalyst["max_strength"]
                + 0.30 * min(catalyst["independent_sources"] / 2, 1)
                + 0.25 * (1.0 if catalyst["structured_preseason_usage"] else 0.0),
            )
        market_cov = 1.0 if mkt is not None else 0.0
        conf = 0.50 * hist_cov + 0.35 * catalyst_quality + 0.15 * market_cov
        row["confidence_score"] = round(conf * 100, 1)
        row["confidence_grade"] = "A" if conf >= .85 else "B" if conf >= .68 else "C" if conf >= .50 else "D"

        if tags:
            rows.append(row)

    priority = {
        "HIGH_PRIORITY_BREAKOUT_ALERT": 7,
        "BREAKOUT_CANDIDATE": 6,
        "WAIVER_TARGET": 5,
        "ROLE_INFLECTION": 4,
        "BUY_LOW": 3,
        "HIDDEN_GEM": 2,
        "HISTORICAL_BREAKOUT_PROFILE": 1,
    }
    def rank_key(r):
        signal_rank = max((priority.get(x, 0) for x in r["signals"]), default=0)
        gap = r["market_mispricing_score"]
        return (
            signal_rank,
            r["confidence_score"] if r["confidence_score"] is not None else 0.0,
            gap if gap is not None else float("-inf"),
        )
    rows.sort(key=rank_key, reverse=True)

    buckets = {}
    for r in rows:
        for tag in r["signals"]:
            buckets.setdefault(tag, []).append(r["player_id"])

    warnings = list(fi.get("warnings") or [])
    if prior_ppg_warning:
        warnings.append(prior_ppg_warning)
    if not calibration:
        warnings.append("HISTORICAL_CALIBRATION_MISSING")

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL,
        "scope": "ALL_ACTIVE_QB_RB_WR_TE",
        "player_universe_count": sum(
            1 for p in players.values()
            if isinstance(p, dict)
            and str(p.get("position", "")).upper() in POSITIONS
            and p.get("active") is not False
        ),
        "candidate_count": len(rows),
        "season_phase": phase,
        "decision_sequence": [
            "HISTORICAL_BREAKOUT_PROFILE",
            "CURRENT_CATALYST",
            "MARKET_LAG",
        ],
        "historical_calibration_model": calibration.get("model_version"),
        "source_coverage": {
            "fsffl_market_players": len(vals),
            "prior_snap_records": int(fi.get("prior_snap_records") or 0),
            "preseason_usage_records": int(fi.get("preseason_usage_records") or 0),
            "manual_intelligence_records": int(fi.get("manual_intelligence_records") or 0),
            "prior_ppg_players": len(prior_ppg),
        },
        "warnings": sorted(set(warnings)),
        "signal_counts": {k: len(v) for k, v in buckets.items()},
        "quality_controls": {
            "breakout_requires_historical_cutoff": True,
            "breakout_requires_current_strong_catalyst": True,
            "high_priority_requires_corroboration": True,
            "market_lag_required_for_breakout": True,
            "historical_profile_alone_is_watchlist": True,
            "prior_year_usage_is_baseline_not_catalyst": True,
            "position_specific_cutoffs_dynamic": True,
        },
        "candidates": rows,
    }
    path = OUT / "emerging_value.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(
        f"Historically calibrated Emerging Value: {len(rows)} candidates "
        f"from {payload['player_universe_count']} players -> {path}"
    )
    print("Signals:", payload["signal_counts"])
    if payload["warnings"]:
        print("Warnings:", ", ".join(payload["warnings"]))

if __name__ == "__main__":
    main()
