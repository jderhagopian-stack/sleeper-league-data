#!/usr/bin/env python3
"""Diagnose whether injury-driven season shocks distort the native-vs-FFToday benchmark.

This is a POST-HOC diagnostic only. Injury and weekly target-season data are never
used to train or alter the preseason projection models. They are used after the
fact to classify player-seasons whose realized outcomes were materially affected
by later injuries or injury-created opportunity.

Cohorts:
- all: exact common benchmark cohort
- stable_no_shock: excludes conservative self-injury and teammate-injury-opportunity flags
- exclude_self_injury: removes only players conservatively flagged for their own later injury
- self_injury: players with >=3 games missed plus at least one later Out/Doubtful injury report
- teammate_injury_opportunity: non-first-team players whose higher-ranked opening teammate
  had >=2 Out/Doubtful weeks and whose weekly opportunity rose materially in those weeks

The aim is interpretation, not score adjustment. We do not retroactively change
any projection or claim an injury was predictable.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from run_native_opening_role_all_vs_fftoday_historical_benchmark import (
    LAYOUT,
    NATIVE_TARGET,
    eligible_inventory,
    fetch_fftoday,
    native_predictions,
    norm_name,
)
from run_native_projection_core_context_benchmark import enrich, fetch_players
from run_native_projection_nflverse_benchmark import TARGETS, fetch_csv, make_lagged_rows, normalize_season
from run_native_projection_opening_role_by_position_benchmark import fetch_depth, rank_num

INJURY_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
WEEKLY_STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
POSITIONS = {"QB", "RB", "WR", "TE"}


def fetch_csv_url(url: str, user_agent: str = "FSFFL-injury-shock-diagnostic/1.0", retries: int = 3) -> list[dict]:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=90) as response:
                return list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
        except Exception as exc:  # network-only retry; preserve final exception
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def fval(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def opening_map(season: int) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for r in fetch_depth(season):
        if str(r.get("game_type") or "").upper() != "REG" or str(r.get("week") or "").strip() != "1":
            continue
        pid = str(r.get("gsis_id") or "").strip()
        pos = str(r.get("position") or "").upper().strip()
        if not pid or pos not in POSITIONS:
            continue
        rank = rank_num(r.get("depth_team"))
        key = (pid, pos)
        prior = out.get(key)
        if prior is None or rank < prior["rank"]:
            out[key] = {
                "rank": rank,
                "team": str(r.get("club_code") or "").strip(),
            }
    return out


def injury_map(season: int) -> dict[str, dict]:
    rows = fetch_csv_url(INJURY_URL.format(season=season))
    out: dict[str, dict] = defaultdict(lambda: {"out_doubtful_weeks": set(), "injury_report_weeks": set()})
    for r in rows:
        if str(r.get("season_type") or r.get("game_type") or "REG").upper() not in {"REG", ""}:
            continue
        pid = str(r.get("gsis_id") or r.get("player_id") or "").strip()
        if not pid:
            continue
        try:
            week = int(float(r.get("week") or 0))
        except (TypeError, ValueError):
            continue
        if week <= 0:
            continue
        primary = str(r.get("report_primary_injury") or r.get("practice_primary_injury") or "").strip()
        secondary = str(r.get("report_secondary_injury") or r.get("practice_secondary_injury") or "").strip()
        report_status = str(r.get("report_status") or "").strip().upper()
        practice_status = str(r.get("practice_status") or "").strip().upper()
        if primary or secondary or report_status or practice_status:
            out[pid]["injury_report_weeks"].add(week)
        if report_status in {"OUT", "DOUBTFUL"}:
            out[pid]["out_doubtful_weeks"].add(week)
    return out


def weekly_opportunity(season: int) -> dict[str, dict[int, float]]:
    rows = fetch_csv_url(WEEKLY_STATS_URL.format(season=season))
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for r in rows:
        pid = str(r.get("player_id") or r.get("gsis_id") or "").strip()
        pos = str(r.get("position") or "").upper().strip()
        if not pid or pos not in POSITIONS:
            continue
        if str(r.get("season_type") or "REG").upper() != "REG":
            continue
        try:
            week = int(float(r.get("week") or 0))
        except (TypeError, ValueError):
            continue
        if pos == "QB":
            opp = fval(r.get("attempts")) + fval(r.get("carries"))
        elif pos == "RB":
            opp = fval(r.get("carries")) + fval(r.get("targets"))
        else:
            opp = fval(r.get("targets")) + fval(r.get("carries"))
        out[pid][week] = opp
    return out


def classify_player_seasons(rows: list[dict], seasons: list[int]) -> dict[tuple[int, str], dict]:
    result: dict[tuple[int, str], dict] = {}
    for season in seasons:
        roles = opening_map(season)
        injuries = injury_map(season)
        weekly = weekly_opportunity(season)
        season_rows = [r for r in rows if int(r["season"]) == season]
        actual_by_pid = {str(r["player_id"]): r for r in season_rows}

        higher_by_team_pos: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
        for (pid, pos), role in roles.items():
            higher_by_team_pos[(role["team"], pos)].append((pid, float(role["rank"])))

        for pid, ar in actual_by_pid.items():
            pos = str(ar.get("position") or "").upper()
            if pos not in POSITIONS:
                continue
            role = roles.get((pid, pos))
            games = fval(ar.get("games"))
            inj = injuries.get(pid, {"out_doubtful_weeks": set(), "injury_report_weeks": set()})
            out_weeks = set(inj["out_doubtful_weeks"])
            self_injury = bool(games <= 14.0 and len(out_weeks) >= 1)

            teammate_shock = False
            higher_injured: list[str] = []
            injury_opp_weeks: set[int] = set()
            if role and float(role["rank"]) > 1.0:
                for other_pid, other_rank in higher_by_team_pos.get((role["team"], pos), []):
                    if other_pid == pid or other_rank >= float(role["rank"]):
                        continue
                    weeks = set(injuries.get(other_pid, {}).get("out_doubtful_weeks", set()))
                    if len(weeks) >= 2:
                        higher_injured.append(other_pid)
                        injury_opp_weeks |= weeks
                if injury_opp_weeks:
                    pweek = weekly.get(pid, {})
                    in_vals = [v for w, v in pweek.items() if w in injury_opp_weeks]
                    out_vals = [v for w, v in pweek.items() if w not in injury_opp_weeks]
                    if in_vals:
                        in_avg = sum(in_vals) / len(in_vals)
                        out_avg = sum(out_vals) / len(out_vals) if out_vals else 0.0
                        # Conservative: require both relative and meaningful absolute usage gain.
                        teammate_shock = bool(
                            len(in_vals) >= 1
                            and in_avg >= max(5.0, out_avg * 1.25)
                            and in_avg - out_avg >= 2.0
                        )

            result[(season, pid)] = {
                "position": pos,
                "games": games,
                "opening_team": role["team"] if role else "",
                "opening_depth_rank": float(role["rank"]) if role else None,
                "self_injury": self_injury,
                "self_out_doubtful_weeks": sorted(out_weeks),
                "teammate_injury_opportunity": teammate_shock,
                "higher_ranked_injured_players": sorted(higher_injured),
                "higher_ranked_out_doubtful_weeks": sorted(injury_opp_weeks),
                "stable_no_shock": not self_injury and not teammate_shock,
            }
    return result


def score_cohort(records: list[dict], predicate, min_group_n: int = 5) -> dict:
    grouped: dict[tuple[int, str, str], list[dict]] = defaultdict(list)
    for r in records:
        if predicate(r):
            grouped[(r["season"], r["position"], r["stat"])].append(r)
    detail = {}
    native_wins = external_wins = ties = 0
    vals = []
    player_seasons = set()
    rows_n = 0
    for key, grp in sorted(grouped.items()):
        # Each player contributes one row to a position/stat/season group.
        if len(grp) < min_group_n:
            continue
        n_mae = sum(abs(x["native"] - x["actual"]) for x in grp) / len(grp)
        e_mae = sum(abs(x["external"] - x["actual"]) for x in grp) / len(grp)
        if abs(n_mae - e_mae) < 1e-12:
            winner = "tie"; ties += 1
        elif n_mae < e_mae:
            winner = "native"; native_wins += 1
        else:
            winner = "external"; external_wins += 1
        rel = 100.0 * (e_mae - n_mae) / e_mae if e_mae else 0.0
        vals.append(rel)
        rows_n += len(grp)
        player_seasons |= {(x["season"], x["player_id"]) for x in grp}
        detail["|".join(map(str, key))] = {
            "n": len(grp), "native_mae": n_mae, "external_mae": e_mae,
            "native_improvement_vs_external_pct": rel, "winner": winner,
        }
    vals_sorted = sorted(vals)
    median = None
    if vals_sorted:
        m = len(vals_sorted) // 2
        median = vals_sorted[m] if len(vals_sorted) % 2 else (vals_sorted[m - 1] + vals_sorted[m]) / 2
    return {
        "group_wins": {"native": native_wins, "external": external_wins, "ties": ties},
        "group_count": native_wins + external_wins + ties,
        "player_seasons": len(player_seasons),
        "scored_player_stat_rows": rows_n,
        "mean_native_improvement_vs_external_pct": (sum(vals) / len(vals)) if vals else None,
        "median_native_improvement_vs_external_pct": median,
        "detail": detail,
    }


def run(inventory_path: Path, start_season: int = 2016) -> dict:
    inv = eligible_inventory(inventory_path)
    target_seasons = sorted({int(x["season"]) for x in inv})
    max_season = max(target_seasons)

    source = []
    for season in range(start_season, max_season + 1):
        source.extend(normalize_season(fetch_csv(season), season))
    rows = enrich(make_lagged_rows(source), source, fetch_players())
    classifications = classify_player_seasons(rows, target_seasons)

    records: list[dict] = []
    for item in inv:
        season = int(item["season"]); pos = item["position"]
        fft = fetch_fftoday(season, pos, item["snapshot_date"])
        nproj = native_predictions(rows, season, pos)
        actual_rows = [r for r in rows if int(r["season"]) == season and r["position"] == pos]
        ai = {norm_name(r["player_name"]): r for r in actual_rows}
        ei = {norm_name(r["player_name"]): r for r in fft}
        common = sorted(set(ai) & set(ei))
        stats = sorted(set(NATIVE_TARGET) & set(dict(LAYOUT[pos])) & {t.removeprefix("next_") for t in TARGETS[pos]})
        for name in common:
            ar = ai[name]; er = ei[name]
            pid = str(ar["player_id"])
            cls = classifications.get((season, pid), {
                "self_injury": False, "teammate_injury_opportunity": False, "stable_no_shock": True,
                "games": fval(ar.get("games")), "position": pos,
            })
            for stat in stats:
                nk = (name, stat); target = NATIVE_TARGET[stat]
                if nk not in nproj or target not in ar or stat not in er:
                    continue
                records.append({
                    "season": season, "position": pos, "player_name": ar["player_name"],
                    "player_id": pid, "stat": stat, "native": float(nproj[nk]),
                    "external": float(er[stat]), "actual": float(ar[target]), **cls,
                })

    cohorts = {
        "all": score_cohort(records, lambda r: True),
        "exclude_self_injury": score_cohort(records, lambda r: not r["self_injury"]),
        "stable_no_shock": score_cohort(records, lambda r: r["stable_no_shock"]),
        "self_injury": score_cohort(records, lambda r: r["self_injury"], min_group_n=3),
        "teammate_injury_opportunity": score_cohort(records, lambda r: r["teammate_injury_opportunity"], min_group_n=3),
    }

    player_flags = {}
    for r in records:
        key = f'{r["season"]}|{r["player_id"]}'
        player_flags.setdefault(key, {
            "season": r["season"], "player_id": r["player_id"], "player_name": r["player_name"],
            "position": r["position"], "games": r["games"], "self_injury": r["self_injury"],
            "self_out_doubtful_weeks": r.get("self_out_doubtful_weeks", []),
            "teammate_injury_opportunity": r["teammate_injury_opportunity"],
            "opening_team": r.get("opening_team", ""), "opening_depth_rank": r.get("opening_depth_rank"),
            "higher_ranked_injured_players": r.get("higher_ranked_injured_players", []),
            "higher_ranked_out_doubtful_weeks": r.get("higher_ranked_out_doubtful_weeks", []),
        })

    all_w = cohorts["all"]["group_wins"]
    stable_w = cohorts["stable_no_shock"]["group_wins"]
    return {
        "schema_version": "1.0",
        "status": "PASS",
        "experiment": "posthoc_injury_shock_diagnostic_native_v2_vs_fftoday",
        "target_seasons": target_seasons,
        "cohorts": cohorts,
        "headline_change": {
            "all_native_wins": all_w["native"], "all_external_wins": all_w["external"],
            "stable_native_wins": stable_w["native"], "stable_external_wins": stable_w["external"],
            "native_win_share_all_pct": 100.0 * all_w["native"] / max(1, all_w["native"] + all_w["external"]),
            "native_win_share_stable_pct": 100.0 * stable_w["native"] / max(1, stable_w["native"] + stable_w["external"]),
        },
        "player_season_flags": list(player_flags.values()),
        "governance": {
            "target_season_injury_data_used_for_training": False,
            "target_season_weekly_data_used_for_training": False,
            "posthoc_only": True,
            "benchmark_projection_values_changed": False,
            "interpretation": "Injury data only partitions realized errors after projections were frozen; it must never be fed backward into historical preseason forecasts.",
        },
        "limitations": [
            "Out/Doubtful injury reports are a conservative proxy for injury-caused missed time and do not capture every IR event or non-injury absence.",
            "Teammate-injury opportunity requires a higher-ranked opening teammate with >=2 Out/Doubtful weeks plus a material observed usage increase; this is intentionally conservative and will miss some true injury-created opportunities.",
            "Historical opening depth charts through 2024 retain the previously documented freeze-timestamp limitation.",
            "This diagnostic changes interpretation of benchmark error, not the preseason model or its production values.",
        ],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inventory", type=Path, default=Path("data/model_validation/historical_projection_source_inventory.json"))
    p.add_argument("--start-season", type=int, default=2016)
    p.add_argument("--output", type=Path, default=Path("data/model_validation/native_v2_fftoday_injury_shock_diagnostic.json"))
    args = p.parse_args()
    data = run(args.inventory, args.start_season)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": data["status"], "headline_change": data["headline_change"], "cohort_counts": {k: {"groups": v["group_count"], "player_seasons": v["player_seasons"]} for k, v in data["cohorts"].items()}}, indent=2))


if __name__ == "__main__":
    main()
