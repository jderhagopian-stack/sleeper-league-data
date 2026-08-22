#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Historical Breakout Calibration

Replaces the old proxy-only audit with a real, reproducible historical backtest.

What it learns:
- Which PRE-SEASON structural/baseline traits historically preceded next-year
  fantasy breakouts for current NFL players.
- Which rookie structural traits historically preceded immediate rookie-year hits.
- False-positive rates for the same signals.
- Empirical likelihood-ratio weights and recommended score thresholds.

Important limitation:
Training-camp buzz, preseason first-team reps, and injury-created opportunity are
not claimed as historically calibrated here unless a historical dataset exists.
Those remain CURRENT CATALYSTS layered on top of this calibrated baseline.

Outputs:
  data/gm/calibration_report.json
  data/gm/breakout_calibration.json
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, date
from pathlib import Path

DATA = Path("data")
OUT = DATA / "gm"
OUT.mkdir(parents=True, exist_ok=True)

PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "player_stats/player_stats.csv.gz"
)
PLAYERS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "players/players.csv"
)
SNAP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "snap_counts/snap_counts_{season}.csv"
)

POSITIONS = {"QB", "RB", "WR", "TE"}
MIN_TRAIN_SEASON = 2018


def load(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def fetch_bytes(url, timeout=60):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FSFFL-GM30-Historical-Calibration/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_csv(url):
    raw = fetch_bytes(url)
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def fnum(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def inum(x, default=None):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def norm_name(x):
    x = str(x or "").lower()
    x = re.sub(r"[^a-z0-9 ]+", "", x)
    return re.sub(r"\s+", " ", x).strip()


def fantasy_points_half_ppr(r):
    return (
        0.04 * fnum(r.get("passing_yards"))
        + 4.0 * fnum(r.get("passing_tds"))
        - 1.0 * fnum(r.get("interceptions"))
        + 0.10 * fnum(r.get("rushing_yards"))
        + 6.0 * fnum(r.get("rushing_tds"))
        + 0.50 * fnum(r.get("receptions"))
        + 0.10 * fnum(r.get("receiving_yards"))
        + 6.0 * fnum(r.get("receiving_tds"))
        - 1.0 * fnum(r.get("rushing_fumbles_lost") or r.get("fumbles_lost"))
    )


def breakout_thresholds(pos):
    # Thresholds are labels for the historical experiment, not learned weights.
    return {
        "QB": {"ppg": 18.0, "prior_ppg_ceiling": 16.0, "delta": 3.5, "major_ppg": 21.0},
        "RB": {"ppg": 11.0, "prior_ppg_ceiling": 9.5, "delta": 3.5, "major_ppg": 14.0},
        "WR": {"ppg": 11.0, "prior_ppg_ceiling": 9.5, "delta": 3.5, "major_ppg": 14.0},
        "TE": {"ppg": 8.0, "prior_ppg_ceiling": 6.5, "delta": 2.5, "major_ppg": 10.5},
    }[pos]


def aggregate_stats(rows, start_season, end_season):
    agg = {}
    for r in rows:
        season = inum(r.get("season"))
        if season is None or season < start_season or season > end_season:
            continue
        if str(r.get("season_type") or r.get("game_type") or "REG").upper() not in {"REG", "REGULAR"}:
            continue
        pos = str(r.get("position") or r.get("position_group") or "").upper()
        if pos not in POSITIONS:
            continue
        pid = str(r.get("player_id") or r.get("gsis_id") or "")
        name = r.get("player_display_name") or r.get("player_name") or r.get("name")
        key = (pid if pid else norm_name(name), season)
        if not key[0]:
            continue
        x = agg.setdefault(key, {
            "player_id": pid or None,
            "name": name,
            "position": pos,
            "season": season,
            "games": set(),
            "fantasy_points": 0.0,
        })
        x["position"] = pos or x["position"]
        week = r.get("week")
        game_id = r.get("game_id")
        if game_id:
            x["games"].add(str(game_id))
        elif week:
            x["games"].add(str(week))
        else:
            # If the feed is seasonal-summary instead of weekly, games may exist.
            gp = inum(r.get("games") or r.get("games_played"))
            if gp is not None:
                x["games"].update(f"g{i}" for i in range(gp))
        x["fantasy_points"] += fantasy_points_half_ppr(r)

    out = {}
    for key, x in agg.items():
        games = len(x["games"])
        if games == 0:
            # Last-resort seasonal row fallback.
            games = 1
        out[key] = {
            **{k: v for k, v in x.items() if k != "games"},
            "games": games,
            "ppg": x["fantasy_points"] / games,
        }
    return out


def aggregate_snaps(seasons, players_by_name):
    by_key = {}
    errors = []
    for season in seasons:
        try:
            rows = fetch_csv(SNAP_URL.format(season=season))
        except Exception as e:
            errors.append({"season": season, "error": str(e)})
            continue
        accum = defaultdict(lambda: {"pct_sum": 0.0, "games": 0, "off_snaps": 0.0})
        for r in rows:
            name = norm_name(r.get("player") or r.get("player_name"))
            if not name:
                continue
            pid = players_by_name.get(name, {}).get("gsis_id")
            key = (pid if pid else name, season)
            accum[key]["pct_sum"] += fnum(r.get("offense_pct") or r.get("offense_snap_pct"))
            accum[key]["off_snaps"] += fnum(r.get("offense_snaps"))
            accum[key]["games"] += 1
        for key, a in accum.items():
            by_key[key] = {
                "snap_share": a["pct_sum"] / max(a["games"], 1),
                "snap_games": a["games"],
                "offense_snaps": a["off_snaps"],
            }
    return by_key, errors


def parse_players(rows):
    by_id = {}
    by_name = {}
    for r in rows:
        name = r.get("display_name") or r.get("full_name") or r.get("name")
        if not name:
            continue
        d = {
            "gsis_id": r.get("gsis_id") or r.get("player_id"),
            "name": name,
            "position": str(r.get("position") or "").upper(),
            "birth_date": r.get("birth_date"),
            "draft_year": inum(r.get("draft_year")),
            "draft_round": inum(r.get("draft_round")),
            "draft_pick": inum(r.get("draft_pick")),
        }
        if d["gsis_id"]:
            by_id[str(d["gsis_id"])] = d
        by_name[norm_name(name)] = d
    return by_id, by_name


def age_on_season(player, season):
    birth = str(player.get("birth_date") or "")
    if len(birth) >= 10:
        try:
            y, m, d = [int(x) for x in birth[:10].split("-")]
            ref = date(season, 9, 1)
            return ref.year - y - ((ref.month, ref.day) < (m, d))
        except Exception:
            pass
    dy = player.get("draft_year")
    if dy is not None:
        # Better than no age: typical rookie age prior.
        return 22 + max(season - dy, 0)
    return None


def player_meta(stat, by_id, by_name):
    pid = stat.get("player_id")
    if pid and str(pid) in by_id:
        return by_id[str(pid)]
    return by_name.get(norm_name(stat.get("name")), {})


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
    if round_ == 1:
        return "ROUND_1"
    if round_ == 2:
        return "ROUND_2"
    if round_ == 3:
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


def bin_ppg(pos, ppg):
    t = breakout_thresholds(pos)
    if ppg < t["prior_ppg_ceiling"] * 0.45:
        return "PPG_LOW"
    if ppg < t["prior_ppg_ceiling"] * 0.80:
        return "PPG_BELOW_AVG"
    if ppg < t["prior_ppg_ceiling"]:
        return "PPG_NEAR_BREAKOUT"
    return "PPG_ALREADY_HIGH"


def bin_experience(exp):
    if exp <= 0:
        return "EXP_ROOKIE"
    if exp == 1:
        return "EXP_YEAR_2"
    if exp == 2:
        return "EXP_YEAR_3"
    if exp <= 4:
        return "EXP_YEAR_4_5"
    return "EXP_YEAR_6_PLUS"


def make_veteran_examples(stats, snaps, by_id, by_name, start, end):
    examples = []
    for (key_id, season), cur in stats.items():
        if season < start or season >= end:
            continue
        pos = cur["position"]
        nxt = stats.get((key_id, season + 1))
        if not nxt or nxt["position"] != pos:
            continue
        if cur["games"] < 4 or nxt["games"] < 6:
            continue

        meta = player_meta(cur, by_id, by_name)
        draft_year = meta.get("draft_year")
        exp = max(season - draft_year, 0) if draft_year is not None else None
        if exp is None or exp < 1:
            # Rookie-year outcomes get a separate cohort.
            continue

        t = breakout_thresholds(pos)
        ppg = cur["ppg"]
        next_ppg = nxt["ppg"]
        delta = next_ppg - ppg
        breakout = (
            next_ppg >= t["ppg"]
            and (ppg < t["prior_ppg_ceiling"] or delta >= t["delta"])
        )
        major = breakout and next_ppg >= t["major_ppg"]

        snap = (snaps.get((key_id, season)) or {}).get("snap_share")
        age = age_on_season(meta, season)
        examples.append({
            "cohort": "VETERAN_NEXT_YEAR",
            "player": cur.get("name"),
            "player_id": cur.get("player_id"),
            "position": pos,
            "season": season,
            "age": age,
            "experience": exp,
            "draft_round": meta.get("draft_round"),
            "prior_ppg": round(ppg, 3),
            "prior_snap_share": round(snap, 4) if snap is not None else None,
            "next_ppg": round(next_ppg, 3),
            "ppg_delta": round(delta, 3),
            "breakout": breakout,
            "major_breakout": major,
            "features": {
                "age": bin_age(age),
                "draft_capital": bin_draft(meta.get("draft_round")),
                "prior_snap": bin_snap(snap),
                "prior_ppg": bin_ppg(pos, ppg),
                "experience": bin_experience(exp),
            },
        })
    return examples


def make_rookie_examples(stats, snaps, by_id, by_name, start, end):
    examples = []
    for pid, meta in by_id.items():
        pos = meta.get("position")
        dy = meta.get("draft_year")
        if pos not in POSITIONS or dy is None or dy < start or dy > end:
            continue
        rookie = stats.get((pid, dy))
        if not rookie or rookie["games"] < 6:
            continue
        t = breakout_thresholds(pos)
        ppg = rookie["ppg"]
        breakout = ppg >= t["ppg"]
        major = ppg >= t["major_ppg"]
        age = age_on_season(meta, dy)
        snap = (snaps.get((pid, dy)) or {}).get("snap_share")
        examples.append({
            "cohort": "ROOKIE_YEAR",
            "player": meta.get("name"),
            "player_id": pid,
            "position": pos,
            "season": dy,
            "age": age,
            "experience": 0,
            "draft_round": meta.get("draft_round"),
            "rookie_ppg": round(ppg, 3),
            "rookie_snap_share": round(snap, 4) if snap is not None else None,
            "breakout": breakout,
            "major_breakout": major,
            "features": {
                "age": bin_age(age),
                "draft_capital": bin_draft(meta.get("draft_round")),
                "experience": "EXP_ROOKIE",
            },
        })
    return examples


def empirical_tables(examples):
    result = {}
    for pos in sorted(POSITIONS):
        rows = [x for x in examples if x["position"] == pos]
        if not rows:
            continue
        overall = sum(x["breakout"] for x in rows) / len(rows)
        major = sum(x["major_breakout"] for x in rows) / len(rows)

        feature_counts = defaultdict(lambda: defaultdict(lambda: {"n": 0, "hits": 0, "major": 0}))
        for x in rows:
            for feature, value in x["features"].items():
                c = feature_counts[feature][value]
                c["n"] += 1
                c["hits"] += int(x["breakout"])
                c["major"] += int(x["major_breakout"])

        tables = {}
        learned = {}
        shrink_k = 20.0
        for feature, values in feature_counts.items():
            tables[feature] = []
            learned[feature] = {}
            for value, c in values.items():
                raw = c["hits"] / c["n"]
                # Beta-like shrinkage toward position baseline to prevent tiny-sample explosions.
                shrunk = (c["hits"] + shrink_k * overall) / (c["n"] + shrink_k)
                lr = shrunk / overall if overall > 0 else 1.0
                weight = max(-1.5, min(1.5, math.log(lr, 2))) if lr > 0 else -1.5
                major_rate = c["major"] / c["n"]
                tables[feature].append({
                    "value": value,
                    "sample": c["n"],
                    "breakouts": c["hits"],
                    "breakout_rate": round(raw, 4),
                    "shrunk_breakout_rate": round(shrunk, 4),
                    "major_breakout_rate": round(major_rate, 4),
                    "likelihood_ratio_vs_position": round(lr, 3),
                    "weight": round(weight, 3),
                })
                learned[feature][value] = round(weight, 3)
            tables[feature].sort(key=lambda r: (-r["shrunk_breakout_rate"], -r["sample"]))

        result[pos] = {
            "sample": len(rows),
            "breakouts": sum(x["breakout"] for x in rows),
            "base_breakout_rate": round(overall, 4),
            "major_breakout_rate": round(major, 4),
            "feature_tables": tables,
            "learned_weights": learned,
        }
    return result


def score_examples(examples, tables):
    scored = []
    for x in examples:
        pos_model = tables.get(x["position"], {})
        weights = pos_model.get("learned_weights", {})
        score = 0.0
        used = 0
        for feature, value in x["features"].items():
            if value in (weights.get(feature) or {}):
                score += weights[feature][value]
                used += 1
        scored.append({**x, "historical_score": round(score, 3), "features_used": used})
    return scored


def threshold_curve(scored):
    out = {}
    for pos in sorted(POSITIONS):
        rows = sorted(
            [x for x in scored if x["position"] == pos],
            key=lambda x: x["historical_score"],
            reverse=True,
        )
        if not rows:
            continue
        base = sum(x["breakout"] for x in rows) / len(rows)
        candidates = []
        for pct in (0.10, 0.15, 0.20, 0.25, 0.33, 0.50):
            n = max(10, int(round(len(rows) * pct)))
            n = min(n, len(rows))
            top = rows[:n]
            precision = sum(x["breakout"] for x in top) / n
            recall = (
                sum(x["breakout"] for x in top) / max(sum(x["breakout"] for x in rows), 1)
            )
            candidates.append({
                "top_fraction": pct,
                "sample": n,
                "score_cutoff": top[-1]["historical_score"],
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "lift_vs_base": round(precision / base, 3) if base > 0 else None,
            })
        # Prefer a selective cutoff with at least ~1.75x base rate; otherwise top 20%.
        viable = [x for x in candidates if (x["lift_vs_base"] or 0) >= 1.75 and x["sample"] >= 10]
        recommended = viable[0] if viable else min(candidates, key=lambda x: abs(x["top_fraction"] - 0.20))
        out[pos] = {
            "base_rate": round(base, 4),
            "curve": candidates,
            "recommended": recommended,
        }
    return out


def top_examples(scored, hit=True, n=25):
    rows = [x for x in scored if bool(x["breakout"]) is hit]
    rows.sort(key=lambda x: x["historical_score"], reverse=True)
    return [{
        "player": x["player"],
        "position": x["position"],
        "season": x["season"],
        "score": x["historical_score"],
        "breakout": x["breakout"],
        "major_breakout": x["major_breakout"],
        "features": x["features"],
    } for x in rows[:n]]


def main():
    league = load(DATA / "league.json", {}) or {}
    active = int(league.get("season") or date.today().year)
    end = active - 1
    start = max(MIN_TRAIN_SEASON, end - 7)

    warnings = []
    try:
        stat_rows = fetch_csv(PLAYER_STATS_URL)
    except Exception as e:
        raise SystemExit(f"Historical calibration failed: player stats unavailable: {e}")

    try:
        player_rows = fetch_csv(PLAYERS_URL)
    except Exception as e:
        raise SystemExit(f"Historical calibration failed: players dataset unavailable: {e}")

    by_id, by_name = parse_players(player_rows)
    stats = aggregate_stats(stat_rows, start, end)
    snaps, snap_errors = aggregate_snaps(range(start, end + 1), by_name)
    if snap_errors:
        warnings.append("SOME_HISTORICAL_SNAP_SEASONS_UNAVAILABLE")

    veteran = make_veteran_examples(stats, snaps, by_id, by_name, start, end)
    rookie = make_rookie_examples(stats, snaps, by_id, by_name, start, end)

    veteran_tables = empirical_tables(veteran)
    rookie_tables = empirical_tables(rookie)
    veteran_scored = score_examples(veteran, veteran_tables)
    rookie_scored = score_examples(rookie, rookie_tables)

    veteran_thresholds = threshold_curve(veteran_scored)
    rookie_thresholds = threshold_curve(rookie_scored)

    calibration = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "FSFFL-GM-3.0-Historical-Breakout-v1",
        "training_window": {"start_season": start, "end_season": end},
        "league_scoring": "0.5 PPR approximation using nflverse box-score fields",
        "method": {
            "label": "position-specific next-year PPG breakout with minimum-games filter",
            "weight_learning": "shrunken empirical likelihood ratio vs position base rate; log2 transformed",
            "false_positives_included": True,
            "current_catalysts_not_claimed_historical": [
                "training_camp_buzz",
                "first_team_reps",
                "injury_opportunity",
                "preseason_role_change",
            ],
        },
        "veteran_next_year": {
            "sample": len(veteran),
            "by_position": veteran_tables,
            "thresholds": veteran_thresholds,
        },
        "rookie_year": {
            "sample": len(rookie),
            "by_position": rookie_tables,
            "thresholds": rookie_thresholds,
        },
        "recommended_live_policy": {
            "historical_baseline": (
                "Use cohort/position learned weights to establish prior breakout likelihood."
            ),
            "current_catalyst": (
                "Treat current camp/preseason/injury signals as evidence multipliers, "
                "not standalone breakout proof."
            ),
            "breakout_candidate": (
                "Require historical score at/above calibrated position cutoff plus at "
                "least one current catalyst; require stronger corroboration if structured "
                "preseason usage is absent."
            ),
            "role_inflection": (
                "Require direct depth-chart proximity or measured role change; generic "
                "injury-ahead signals are insufficient."
            ),
        },
        "warnings": warnings,
    }

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "FSFFL-GM-3.0-Historical-Breakout-v1",
        "status": "HISTORICAL_BACKTEST_COMPLETE",
        "training_window": calibration["training_window"],
        "veteran_sample": len(veteran),
        "rookie_sample": len(rookie),
        "veteran_position_summary": {
            p: {
                "sample": v["sample"],
                "base_breakout_rate": v["base_breakout_rate"],
                "recommended_cutoff": veteran_thresholds.get(p, {}).get("recommended"),
            }
            for p, v in veteran_tables.items()
        },
        "rookie_position_summary": {
            p: {
                "sample": v["sample"],
                "base_breakout_rate": v["base_breakout_rate"],
                "recommended_cutoff": rookie_thresholds.get(p, {}).get("recommended"),
            }
            for p, v in rookie_tables.items()
        },
        "top_historical_true_positives": top_examples(veteran_scored, True, 20),
        "top_historical_false_positives": top_examples(veteran_scored, False, 20),
        "limitations": [
            "Historical camp-report text is not available in a standardized training set.",
            "Historical dynasty market values are not used, so this calibrates breakout probability rather than market mispricing.",
            "Preseason usage is not yet part of the historical training set.",
            "Current-catalyst parameters must remain conservative until those datasets are added.",
        ],
        "warnings": warnings,
    }

    dump(OUT / "breakout_calibration.json", calibration)
    dump(OUT / "calibration_report.json", report)

    print(
        "Historical breakout calibration complete: "
        f"{len(veteran)} veteran season-pairs + {len(rookie)} rookie seasons "
        f"({start}-{end})."
    )
    for pos in sorted(veteran_thresholds):
        rec = veteran_thresholds[pos]["recommended"]
        print(
            f"{pos}: base={veteran_thresholds[pos]['base_rate']:.3f} "
            f"recommended cutoff={rec['score_cutoff']:.3f} "
            f"precision={rec['precision']:.3f} lift={rec['lift_vs_base']}"
        )


if __name__ == "__main__":
    main()
