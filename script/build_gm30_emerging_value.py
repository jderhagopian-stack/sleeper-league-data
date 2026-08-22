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
MODEL = "FSFFL-GM-3.0-Emerging-Value-v5.0-Developmental-Emergence"
POSITIONS = {"QB", "RB", "WR", "TE"}
PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
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

def current_nfl_team(p, v):
    team = first(p, "team", default=first(v, "nfl_team"))
    team = str(team or "").strip().upper()
    if team in {"", "FA", "FREE_AGENT", "FREE AGENT", "UNK", "UNKNOWN", "N/A", "NA", "NONE"}:
        return None
    return team

def dynasty_stash_eligible(pos, age, exp):
    # "Stash" is a developmental label, not a generic value label.
    age_limits = {"QB": 26, "RB": 24, "WR": 25, "TE": 26}
    if age is None:
        return exp is not None and exp <= 3
    if age > age_limits.get(pos, 25):
        return False
    return exp is None or exp <= 4


def developmental_experience(exp):
    """Emerging Value is for post-rookie players in NFL Years 2-5.

    Sleeper years_exp=1 means one completed NFL season / entering Year 2.
    Rookies (0) belong to the separate prospect/rookie model.
    """
    return exp is not None and 1 <= exp <= 4

def extraordinary_veteran_circumstance(exp, catalyst):
    """Year-5+ players only enter Emerging Value on exceptional new evidence."""
    if exp is None or exp <= 4:
        return False
    return bool(
        catalyst.get("strong")
        and catalyst.get("corroborated")
        and catalyst.get("positive_independent_sources", 0) >= 2
        and catalyst.get("max_positive_strength", 0) >= 0.80
    )

def positive_signal_types_from_manual(m):
    if not isinstance(m, dict):
        return set()
    evidence = m.get("evidence") if isinstance(m.get("evidence"), list) else []
    out = set()
    for x in evidence:
        if not isinstance(x, dict):
            continue
        sig = str(
            x.get("signal_type")
            or x.get("type")
            or x.get("signal")
            or ""
        )
        if sig:
            out.add(sig)
    # Also support boolean-style manual-intelligence records.
    for sig in (
        "depth_chart_rise",
        "starter_reps",
        "camp_buzz",
        "preseason_role",
        "injury_opportunity",
        "coach_praise",
    ):
        if m.get(sig):
            out.add(sig)
    return out

def credible_path_to_relevance(pos, catalyst, pre, m):
    """Require a plausible near-term route to fantasy-relevant opportunity.

    This deliberately avoids treating prior-year snap share as current depth-chart
    evidence. For QBs, generic injury opportunity alone is not enough to turn a
    QB3/QB4 profile into an ADD.
    """
    sigs = positive_signal_types_from_manual(m)
    pre_strong = bool(
        isinstance(pre, dict)
        and pre.get("meaningful_role_signal")
        and num(pre.get("signal_strength"), 0) >= 0.75
    )

    if pos == "QB":
        direct_qb_path = bool(
            {"starter_reps", "depth_chart_rise"} & sigs
        )
        corroborated_qb_path = bool(
            pre_strong
            and (
                "preseason_role" in sigs
                or "injury_opportunity" in sigs
            )
            and catalyst.get("corroborated")
        )
        return direct_qb_path or corroborated_qb_path

    direct_skill_path = bool(
        {"starter_reps", "depth_chart_rise", "preseason_role"} & sigs
    )
    injury_path = bool(
        "injury_opportunity" in sigs
        and (
            catalyst.get("corroborated")
            or pre_strong
        )
    )
    return direct_skill_path or injury_path or pre_strong

def developmental_trajectory_score(hist, catalyst, uscore, exp):
    """0-1 summary used for young-player developmental ranking."""
    hist_score = 0.5
    cutoff = hist.get("cutoff")
    if cutoff is not None:
        hist_score = clamp((num(hist.get("score"), 0) - float(cutoff)) / 3 + 0.5)

    experience_bonus = {
        1: 1.00,  # entering Year 2
        2: 0.95,  # entering Year 3
        3: 0.80,  # entering Year 4
        4: 0.65,  # entering Year 5 / final normal-eligibility band
    }.get(int(exp) if exp is not None else -1, 0.0)

    current = num(catalyst.get("max_positive_strength"), 0)
    usage = uscore if uscore is not None else 0.5

    return clamp(
        0.38 * hist_score
        + 0.24 * experience_bonus
        + 0.23 * current
        + 0.15 * usage
    )

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
    """Fetch prior completed regular-season half-PPR PPG by normalized player name.

    nflverse schemas can vary slightly by vintage, so this reader is intentionally
    tolerant about season-type labels, position columns, and interception fields.
    """
    diagnostics = {
        "target_season": int(active_season) - 1,
        "rows_seen": 0,
        "target_season_rows": 0,
        "regular_season_rows": 0,
        "fantasy_position_rows": 0,
        "players_aggregated": 0,
    }
    try:
        source_url = PLAYER_STATS_URL.format(season=int(active_season) - 1)
        diagnostics["source_url"] = source_url
        req = urllib.request.Request(
            source_url,
            headers={"User-Agent": "FSFFL-GM30-Emerging/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = r.read()
        rows = csv.DictReader(io.StringIO(payload.decode("utf-8")))
    except Exception as e:
        diagnostics["error"] = str(e)
        return {}, "PRIOR_PPG_FETCH_FAILED", diagnostics

    prior = int(active_season) - 1
    agg = {}
    games = {}

    regular_labels = {
        "", "REG", "REGULAR", "REGULAR_SEASON", "REGULAR SEASON", "RS"
    }

    for r in rows:
        diagnostics["rows_seen"] += 1

        try:
            season = int(float(r.get("season") or 0))
        except Exception:
            continue
        if season != prior:
            continue
        diagnostics["target_season_rows"] += 1

        season_type = str(
            r.get("season_type")
            or r.get("game_type")
            or r.get("season_phase")
            or ""
        ).strip().upper()
        if season_type not in regular_labels:
            continue
        diagnostics["regular_season_rows"] += 1

        # Position is used only as a relevance filter. Accept all common field names.
        pos = str(
            r.get("position")
            or r.get("position_group")
            or r.get("fantasy_position")
            or ""
        ).strip().upper()
        if pos and pos not in POSITIONS:
            continue

        name = norm_name(
            r.get("player_display_name")
            or r.get("player_name")
            or r.get("display_name")
            or r.get("name")
        )
        if not name:
            continue

        # If position is missing entirely, include the row; current Sleeper-player
        # matching later prevents non-fantasy positions from affecting live players.
        diagnostics["fantasy_position_rows"] += 1

        fp = (
            0.04 * num(r.get("passing_yards"), 0)
            + 4.0 * num(r.get("passing_tds"), 0)
            - 1.0 * num(
                r.get("passing_interceptions")
                if r.get("passing_interceptions") is not None
                else r.get("interceptions"),
                0,
            )
            + 0.10 * num(r.get("rushing_yards"), 0)
            + 6.0 * num(r.get("rushing_tds"), 0)
            + 0.50 * num(r.get("receptions"), 0)
            + 0.10 * num(r.get("receiving_yards"), 0)
            + 6.0 * num(r.get("receiving_tds"), 0)
            - 1.0 * num(
                r.get("rushing_fumbles_lost")
                or r.get("receiving_fumbles_lost")
                or r.get("fumbles_lost"),
                0,
            )
        )

        agg[name] = agg.get(name, 0.0) + fp

        game_key = str(
            r.get("game_id")
            or r.get("week")
            or r.get("game_week")
            or ""
        )
        if not game_key:
            game_key = f"row-{diagnostics['fantasy_position_rows']}"
        games.setdefault(name, set()).add(game_key)

    result = {
        name: agg[name] / max(len(games.get(name, set())), 1)
        for name in agg
    }
    diagnostics["players_aggregated"] = len(result)

    warning = None
    if diagnostics["target_season_rows"] == 0:
        warning = "PRIOR_PPG_TARGET_SEASON_NOT_FOUND"
    elif diagnostics["regular_season_rows"] == 0:
        warning = "PRIOR_PPG_REGULAR_SEASON_FILTER_EMPTY"
    elif not result:
        warning = "PRIOR_PPG_NO_PLAYERS_AGGREGATED"

    return result, warning, diagnostics

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
    ppg_bin = None
    already_established = False
    if not rookie:
        features["prior_snap"] = bin_snap(prior_snap)
        ppg_bin = bin_ppg(pos, prior_ppg)
        already_established = ppg_bin == "PPG_ALREADY_HIGH"
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
    clears = (
        threshold is not None
        and score >= float(threshold)
        and not already_established
    )
    return {
        "cohort": "ROOKIE_YEAR" if rookie else "VETERAN_NEXT_YEAR",
        "score": round(score, 3),
        "cutoff": float(threshold) if threshold is not None else None,
        "clears_cutoff": bool(clears),
        "already_established": already_established,
        "disqualifiers": ["PRIOR_PPG_ALREADY_HIGH"] if already_established else [],
        "feature_coverage": round(coverage, 2),
        "features_used": used,
        "contributions": contributions,
        "base_breakout_rate": pos_model.get("base_breakout_rate"),
    }

def catalyst_profile(m, pre):
    evidence = m.get("evidence") if isinstance(m, dict) else []
    evidence = evidence if isinstance(evidence, list) else []

    positive_signal_types = {
        "depth_chart_rise",
        "starter_reps",
        "camp_buzz",
        "preseason_role",
        "injury_opportunity",
        "coach_praise",
    }
    negative_signal_types = {
        "depth_chart_fall",
        "injury_concern",
        "role_loss",
    }

    positive_evidence = []
    negative_evidence = []

    for x in evidence:
        if not isinstance(x, dict):
            continue

        signal_type = str(
            x.get("signal_type")
            or x.get("type")
            or x.get("signal")
            or ""
        )

        if signal_type in negative_signal_types:
            negative_evidence.append(x)
        elif signal_type in positive_signal_types:
            positive_evidence.append(x)
        else:
            # Unknown evidence may remain informational, but it cannot
            # corroborate a positive breakout thesis.
            continue

    positive_strengths = [
        num(x.get("strength"), 0) for x in positive_evidence
    ]
    negative_strengths = [
        num(x.get("strength"), 0) for x in negative_evidence
    ]

    positive_max = max(positive_strengths) if positive_strengths else 0.0
    negative_max = max(negative_strengths) if negative_strengths else 0.0

    positive_strong_count = sum(
        1 for x in positive_strengths if x >= 0.75
    )

    positive_sources = {
        str(x.get("source"))
        for x in positive_evidence
        if x.get("source")
    }

    structured_preseason = bool(
        isinstance(pre, dict)
        and pre.get("meaningful_role_signal")
    )

    preseason_strength = (
        num(pre.get("signal_strength"), 0)
        if structured_preseason
        else 0.0
    )

    # A structured preseason result must actually be strong enough.
    preseason_strong = (
        structured_preseason
        and preseason_strength >= 0.75
    )

    max_positive_strength = max(
        positive_max,
        preseason_strength,
    )

    # Strong means at least one genuinely strong POSITIVE catalyst.
    strong = (
        preseason_strong
        or positive_max >= 0.75
    )

    # Corroboration must come from independent POSITIVE evidence.
    # Negative evidence can never corroborate a breakout thesis.
    corroborated = (
        (
            preseason_strong
            and len(positive_sources) >= 1
        )
        or positive_strong_count >= 2
        or len(positive_sources) >= 2
    )

    # Strong negative evidence can veto a positive breakout catalyst
    # unless there is genuinely corroborated positive evidence.
    negative_veto = (
        negative_max >= 0.75
        and not corroborated
    )

    if negative_veto:
        strong = False
        corroborated = False

    return {
        "present": (
            structured_preseason
            or bool(positive_evidence)
            or bool(negative_evidence)
        ),
        "structured_preseason_usage": structured_preseason,
        "preseason_signal_strength": round(
            preseason_strength, 2
        ),
        "preseason_signal_reasons": (
            list(pre.get("signal_reasons") or [])
            if isinstance(pre, dict)
            else []
        ),
        "evidence_count": len(evidence),
        "positive_evidence_count": len(positive_evidence),
        "negative_evidence_count": len(negative_evidence),
        "strong_positive_evidence_count": positive_strong_count,
        "positive_independent_sources": len(positive_sources),
        "max_positive_strength": round(
            max_positive_strength, 2
        ),
        "max_negative_strength": round(
            negative_max, 2
        ),
        "negative_veto": negative_veto,
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
    exp = row["years_exp"]

    hist_ok = bool(hist.get("clears_cutoff"))
    hist_coverage = float(hist.get("feature_coverage") or 0)
    strong_now = bool(catalyst.get("strong"))
    corroborated_now = bool(catalyst.get("corroborated"))
    credible_path = bool(row.get("credible_path_to_relevance"))
    developmental = bool(row.get("developmental_eligible"))
    extraordinary_veteran = bool(row.get("extraordinary_veteran_circumstance"))
    trajectory = num(row.get("developmental_trajectory_score"), 0)

    negative_now = (
        int(catalyst.get("negative_evidence_count") or 0) > 0
        or bool(catalyst.get("negative_veto"))
    )

    # Core v5.0 watchlist: young post-rookie players whose historical/developmental
    # shape is interesting. This is NOT an acquisition recommendation by itself.
    if developmental and hist_ok:
        tags.append("DEVELOPMENTAL_WATCH")

    # Stronger developmental emergence requires a credible current opportunity path,
    # but does not yet require the market-dislocation threshold for a breakout call.
    if (
        developmental
        and hist_ok
        and credible_path
        and trajectory >= 0.62
        and not negative_now
    ):
        tags.append("DEVELOPMENTAL_EMERGING")

    # Hidden gem remains a market/value label but is restricted to the intended
    # young-player universe unless a veteran has an extraordinary circumstance.
    if (
        gap is not None
        and gap >= 0.25
        and latent >= 0.62
        and not negative_now
        and (developmental or extraordinary_veteran)
        and credible_path
    ):
        tags.append("HIDDEN_GEM")

    # Breakout = developmental profile + strong present evidence + actual path +
    # market lag. No credible path means no breakout recommendation.
    if (
        developmental
        and hist_ok
        and strong_now
        and credible_path
        and gap is not None
        and gap >= 0.08
        and not negative_now
    ):
        tags.append("BREAKOUT_CANDIDATE")

    if (
        developmental
        and hist_ok
        and corroborated_now
        and credible_path
        and gap is not None
        and gap >= 0.15
        and not negative_now
    ):
        tags.append("HIGH_PRIORITY_BREAKOUT_ALERT")

    # Waiver recommendations require real opportunity. This is the Brady Cook fix.
    if (
        not rostered
        and developmental
        and hist_ok
        and strong_now
        and credible_path
        and not negative_now
    ):
        tags.append("WAIVER_TARGET")

    # Young-player buy-low only; older-player value dislocations belong in the
    # broader GM/trade model unless the veteran exception is truly extraordinary.
    if (
        rostered
        and gap is not None
        and gap >= 0.18
        and hist_ok
        and developmental
    ):
        tags.append("BUY_LOW")

    if (
        developmental
        and hist_ok
        and hist_coverage >= 0.67
        and mkt is not None
        and mkt <= 0.40
        and dynasty_stash_eligible(row["position"], row["age"], exp)
    ):
        tags.append("DYNASTY_STASH")

    # Role inflection is allowed for developmental players, or for Year-5+ players
    # only if the extraordinary-circumstance gate is satisfied.
    if (
        credible_path
        and strong_now
        and row["manual_score"] is not None
        and row["manual_score"] >= 0.68
        and (developmental or extraordinary_veteran)
        and not negative_now
    ):
        tags.append("ROLE_INFLECTION")

    if extraordinary_veteran and credible_path and strong_now and not negative_now:
        tags.append("EXTRAORDINARY_VETERAN_EVENT")

    # Negative market-risk tags remain conservative and league-wide. These are not
    # "emerging value" recommendations but remain useful risk outputs.
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
    elif any(
        x in tags for x in {
            "BREAKOUT_CANDIDATE",
            "DEVELOPMENTAL_EMERGING",
            "HIDDEN_GEM",
            "BUY_LOW",
            "DYNASTY_STASH",
            "ROLE_INFLECTION",
            "EXTRAORDINARY_VETERAN_EVENT",
        }
    ):
        direction = "ACQUIRE"
    elif "DEVELOPMENTAL_WATCH" in tags:
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
    prior_ppg, prior_ppg_warning, prior_ppg_diagnostics = fetch_prior_ppg(active_season)
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

        v = vals.get(str(pid), {})
        nfl_team = current_nfl_team(p, v)
        if nfl_team is None:
            continue

        age = num(p.get("age"))
        exp = num(first(p, "years_exp", "experience"))

        # Rookies are owned by the separate Prospect/Rookie Intelligence layer.
        if exp is not None and exp <= 0:
            continue

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
        credible_path = credible_path_to_relevance(pos, catalyst, pre, m)
        developmental = developmental_experience(exp)
        extraordinary_veteran = extraordinary_veteran_circumstance(exp, catalyst)
        trajectory = developmental_trajectory_score(
            hist, catalyst, uscore, exp
        ) if developmental else 0.0

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
            "nfl_team": nfl_team,
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
            "developmental_eligible": developmental,
            "extraordinary_veteran_circumstance": extraordinary_veteran,
            "credible_path_to_relevance": credible_path,
            "developmental_trajectory_score": round(trajectory * 100, 1),
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
                0.45 * catalyst["max_positive_strength"]
                + 0.30 * min(catalyst["positive_independent_sources"] / 2, 1)
                + 0.25 * (1.0 if catalyst["structured_preseason_usage"] else 0.0),
            )
        market_cov = 1.0 if mkt is not None else 0.0
        conf = 0.50 * hist_cov + 0.35 * catalyst_quality + 0.15 * market_cov
        row["confidence_score"] = round(conf * 100, 1)
        row["confidence_grade"] = "A" if conf >= .85 else "B" if conf >= .68 else "C" if conf >= .50 else "D"

        if tags:
            rows.append(row)

    priority = {
        "HIGH_PRIORITY_BREAKOUT_ALERT": 9,
        "BREAKOUT_CANDIDATE": 8,
        "WAIVER_TARGET": 7,
        "DEVELOPMENTAL_EMERGING": 6,
        "ROLE_INFLECTION": 5,
        "BUY_LOW": 4,
        "HIDDEN_GEM": 3,
        "EXTRAORDINARY_VETERAN_EVENT": 2,
        "DEVELOPMENTAL_WATCH": 1,
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
        "scope": "POST_ROOKIE_DEVELOPMENTAL_YEARS_1_TO_4_PLUS_EXTRAORDINARY_VETERANS",
        "player_universe_count": sum(
            1
            for pid, p in players.items()
            if isinstance(p, dict)
            and str(p.get("position", "")).upper() in POSITIONS
            and p.get("active") is not False
            and current_nfl_team(p, vals.get(str(pid), {})) is not None
        ),
        "candidate_count": len(rows),
        "season_phase": phase,
        "decision_sequence": [
            "DEVELOPMENTAL_ELIGIBILITY",
            "HISTORICAL_BREAKOUT_CALIBRATION",
            "CURRENT_CATALYST",
            "CREDIBLE_PATH_TO_RELEVANCE",
            "MARKET_LAG",
        ],
        "historical_calibration_model": calibration.get("model_version"),
        "methodology_note": (
            "Emerging Value v5.0 targets post-rookie NFL development. "
            "Years-exp 1-4 are the normal universe; rookies belong to the separate "
            "prospect model; Year-5+ players require extraordinary corroborated "
            "circumstances. Historical profile is an input, not an action signal."
        ),
        "source_coverage": {
            "fsffl_market_players": len(vals),
            "prior_snap_records": int(fi.get("prior_snap_records") or 0),
            "preseason_usage_records": int(fi.get("preseason_usage_records") or 0),
            "manual_intelligence_records": int(fi.get("manual_intelligence_records") or 0),
            "prior_ppg_players": len(prior_ppg),
            "prior_ppg_diagnostics": prior_ppg_diagnostics,
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
            "already_established_disqualified_from_breakout_profile": True,
            "hidden_gem_blocked_by_negative_current_evidence": True,
            "actionable_candidates_require_current_nfl_team": True,
            "dynasty_stash_requires_developmental_age_profile": True,
            "rookies_excluded_use_prospect_model": True,
            "normal_emerging_value_universe_years_exp_1_to_4": True,
            "year_5_plus_requires_extraordinary_circumstance": True,
            "action_recommendations_require_credible_path_to_relevance": True,
            "qb_injury_opportunity_alone_not_sufficient_for_add": True,
            "developmental_trajectory_is_core_signal": True,
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
