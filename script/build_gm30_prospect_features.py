#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Prospect Feature Enrichment v1

Reliable automated sources
--------------------------
1) nflverse draft picks (PFR-backed):
   https://raw.githubusercontent.com/nflverse/nfldata/master/data/draft_picks.csv
2) nflverse combine dataset (PFR-backed):
   https://github.com/nflverse/nflverse-data/releases/download/combine/combine.csv
3) SportsDataverse / cfbfastR player stats:
   https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/
       player_stats/parquet/player_stats_<college_season>.parquet

Design principles
-----------------
- Exact/normalized name + position matching; no blind fuzzy assignment.
- Never substitute FSFFL rookie draft position for NFL draft capital.
- College production is converted into transparent team-share / efficiency
  features, then percentile-normalized within fantasy position.
- Combine athleticism is a position-relative composite when measurements exist.
- Missing data stays missing. The downstream prospect engine's coverage gate
  decides whether the evidence is sufficient for a strong signal.
"""
from __future__ import annotations

import io
import json
import math
import re
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data")
INPUTS = DATA / "gm3_prospect_inputs.json"
AUDIT = DATA / "gm3_prospect_feature_audit.json"

DRAFT_URL = (
    "https://raw.githubusercontent.com/nflverse/nfldata/master/data/draft_picks.csv"
)
COMBINE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "combine/combine.csv"
)
CFB_PARQUET = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
    "player_stats/parquet/player_stats_{season}.parquet"
)

POSITIONS = {"QB", "RB", "WR", "TE"}


def load(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save(path, obj):
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def norm_name(x):
    s = unicodedata.normalize("NFKD", str(x or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("’", "'")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_pos(x):
    p = str(x or "").upper().strip()
    aliases = {
        "HB": "RB", "FB": "RB",
        "FL": "WR", "SE": "WR",
    }
    return aliases.get(p, p)


def numeric(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def read_csv_url(url):
    return pd.read_csv(url, low_memory=False)


def read_parquet_url(url):
    with urllib.request.urlopen(url, timeout=90) as r:
        raw = r.read()
    return pd.read_parquet(io.BytesIO(raw))


def first_col(df, *names):
    lower = {str(c).lower(): c for c in df.columns}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def pct_rank(values, higher_better=True):
    """
    Return a float64 NumPy array with np.nan for missing values.

    pandas 2.x/3.x will reject assigning Python None values into an existing
    float64 column via .loc because that would require a lossy dtype change.
    Keeping the result numeric avoids that failure and preserves missingness.
    """
    ser = pd.to_numeric(pd.Series(values), errors="coerce").astype("float64")
    if ser.notna().sum() <= 1:
        return np.where(ser.notna().to_numpy(), 0.5, np.nan).astype("float64")
    ranks = ser.rank(method="average", pct=True, ascending=higher_better)
    return ranks.to_numpy(dtype="float64")


def safe_div(a, b):
    a = numeric(a)
    b = numeric(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def aggregate_player_stats(df):
    """
    cfbfastR's legacy player_stats data has evolved over time, so this routine
    accepts common schema aliases and only computes features whose raw columns
    genuinely exist.
    """
    name_col = first_col(
        df,
        "player", "player_name", "athlete_name", "name",
    )
    pos_col = first_col(df, "position", "pos")
    team_col = first_col(
        df,
        "team", "team_name", "school", "pos_team",
    )

    if not name_col:
        # Common cfbfastR files separate stat-category player-name columns.
        # We construct long player rows from whichever offensive groups exist.
        frames = []

        def category_frame(player_candidates, mapping, forced_pos=None):
            pcol = first_col(df, *player_candidates)
            if not pcol:
                return
            cols = {k: first_col(df, *aliases) for k, aliases in mapping.items()}
            use = [pcol] + ([team_col] if team_col else []) + [
                c for c in cols.values() if c
            ]
            sub = df[use].copy()
            sub = sub[sub[pcol].notna()]
            sub["__name"] = sub[pcol]
            sub["__position"] = forced_pos
            sub["__team"] = sub[team_col] if team_col else None
            for out, c in cols.items():
                sub[out] = pd.to_numeric(sub[c], errors="coerce") if c else np.nan
            frames.append(
                sub[
                    ["__name", "__position", "__team"] + list(mapping.keys())
                ]
            )

        category_frame(
            ["passer_player_name", "passer_player", "passing_player"],
            {
                "pass_attempts": ["pass_attempts", "passing_att", "att"],
                "pass_completions": ["pass_completions", "passing_completions", "comp"],
                "pass_yards": ["pass_yards", "passing_yards", "yards"],
                "pass_td": ["pass_touchdowns", "passing_touchdowns", "passing_td"],
                "interceptions": ["interceptions", "pass_interceptions", "pass_int"],
                "sacks": ["sacks", "sacked"],
                "rush_attempts": ["rush_attempts", "rushing_attempts", "carries"],
                "rush_yards": ["rush_yards", "rushing_yards"],
                "rush_td": ["rush_touchdowns", "rushing_touchdowns"],
            },
            "QB",
        )
        category_frame(
            ["rusher_player_name", "rusher_player", "rushing_player"],
            {
                "rush_attempts": ["rush_attempts", "rushing_attempts", "carries"],
                "rush_yards": ["rush_yards", "rushing_yards"],
                "rush_td": ["rush_touchdowns", "rushing_touchdowns"],
                "receptions": ["receptions", "receiving_rec"],
                "rec_yards": ["receiving_yards", "rec_yards"],
                "rec_td": ["receiving_touchdowns", "rec_touchdowns"],
                "targets": ["targets", "receiving_targets"],
            },
            "RB",
        )
        category_frame(
            ["receiver_player_name", "receiver_player", "receiving_player"],
            {
                "receptions": ["receptions", "receiving_rec"],
                "rec_yards": ["receiving_yards", "rec_yards"],
                "rec_td": ["receiving_touchdowns", "rec_touchdowns"],
                "targets": ["targets", "receiving_targets"],
            },
            None,
        )

        if not frames:
            return pd.DataFrame()

        long = pd.concat(frames, ignore_index=True, sort=False)
        long["__norm_name"] = long["__name"].map(norm_name)
        # Position may be unknown for receivers. Leave it null; matching can use name.
        stat_cols = [
            c for c in long.columns
            if c not in {"__name", "__position", "__team", "__norm_name"}
        ]
        agg = {
            c: "sum" for c in stat_cols
        }
        agg["__name"] = "first"
        agg["__position"] = "first"
        agg["__team"] = "first"
        return long.groupby("__norm_name", as_index=False).agg(agg)

    out = pd.DataFrame()
    out["__name"] = df[name_col]
    out["__norm_name"] = out["__name"].map(norm_name)
    out["__position"] = df[pos_col].map(norm_pos) if pos_col else None
    out["__team"] = df[team_col] if team_col else None

    aliases = {
        "games": ["games", "games_played"],
        "pass_attempts": ["pass_attempts", "passing_att", "passing_attempts", "att"],
        "pass_completions": ["pass_completions", "passing_completions", "comp"],
        "pass_yards": ["pass_yards", "passing_yards"],
        "pass_td": ["pass_touchdowns", "passing_touchdowns", "passing_td"],
        "interceptions": ["interceptions", "pass_interceptions", "pass_int"],
        "sacks": ["sacks", "sacked"],
        "rush_attempts": ["rush_attempts", "rushing_attempts", "carries"],
        "rush_yards": ["rush_yards", "rushing_yards"],
        "rush_td": ["rush_touchdowns", "rushing_touchdowns"],
        "receptions": ["receptions", "receiving_rec"],
        "rec_yards": ["receiving_yards", "rec_yards"],
        "rec_td": ["receiving_touchdowns", "rec_touchdowns"],
        "targets": ["targets", "receiving_targets"],
    }

    for dest, opts in aliases.items():
        c = first_col(df, *opts)
        out[dest] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    stat_cols = list(aliases.keys())
    agg = {c: "sum" for c in stat_cols}
    agg["__name"] = "first"
    agg["__position"] = "first"
    agg["__team"] = "first"
    return out.groupby("__norm_name", as_index=False).agg(agg)


def add_team_shares(stats):
    if stats.empty or "__team" not in stats:
        return stats

    stats = stats.copy()

    for raw, out in (
        ("rec_yards", "receiving_yards_share"),
        ("rec_td", "receiving_td_share"),
        ("targets", "target_share"),
        ("rush_yards", "rushing_yards_share"),
        ("rush_td", "rushing_td_share"),
    ):
        if raw not in stats:
            stats[out] = np.nan
            continue
        totals = stats.groupby("__team")[raw].transform("sum")
        stats[out] = stats[raw] / totals.replace(0, np.nan)

    stats["dominator"] = (
        stats[["receiving_yards_share", "receiving_td_share"]]
        .mean(axis=1, skipna=True)
    )
    stats["scrimmage_yards"] = (
        stats.get("rush_yards", 0).fillna(0)
        + stats.get("rec_yards", 0).fillna(0)
    )
    team_scrimmage = stats.groupby("__team")["scrimmage_yards"].transform("sum")
    stats["scrimmage_yards_share"] = (
        stats["scrimmage_yards"] / team_scrimmage.replace(0, np.nan)
    )

    stats["yards_per_carry"] = (
        stats["rush_yards"] / stats["rush_attempts"].replace(0, np.nan)
    )
    stats["yards_per_target"] = (
        stats["rec_yards"] / stats["targets"].replace(0, np.nan)
    )
    stats["completion_pct"] = (
        stats["pass_completions"] / stats["pass_attempts"].replace(0, np.nan)
    )
    stats["yards_per_attempt"] = (
        stats["pass_yards"] / stats["pass_attempts"].replace(0, np.nan)
    )
    dropbacks = stats["pass_attempts"].fillna(0) + stats["sacks"].fillna(0)
    stats["sack_rate_inverse_raw"] = 1.0 - (
        stats["sacks"].fillna(0) / dropbacks.replace(0, np.nan)
    )
    td = stats["pass_td"].fillna(0)
    ints = stats["interceptions"].fillna(0)
    stats["td_int_efficiency_raw"] = (td + 1.0) / (td + ints + 2.0)
    return stats


def position_percentiles(stats, field_map):
    """
    Create 0..1 normalized feature columns. If cfb position is missing for
    receivers, rows can still be matched later and percentile is taken over
    the whole receiving cohort for receiving-only metrics.
    """
    stats = stats.copy()
    for raw, dest, higher in field_map:
        if raw not in stats:
            stats[dest] = np.nan
            continue

        # Percentile within stated position where possible.
        vals = pd.Series(index=stats.index, dtype="float64")
        stated = stats["__position"].fillna("UNKNOWN")
        for pos in stated.unique():
            idx = stats.index[stated == pos]
            if pos == "UNKNOWN" or len(idx) < 10:
                cohort = stats[raw]
                ranked = pd.Series(pct_rank(cohort, higher), index=stats.index)
                vals.loc[idx] = ranked.loc[idx]
            else:
                vals.loc[idx] = pct_rank(stats.loc[idx, raw], higher)
        stats[dest] = vals
    return stats


def draft_map(df, season):
    if df.empty:
        return {}
    season_col = first_col(df, "season", "draft_year")
    name_col = first_col(df, "full_name", "name")
    pos_col = first_col(df, "position", "category")
    pick_col = first_col(df, "pick", "overall")
    round_col = first_col(df, "round")

    out = {}
    if not all([season_col, name_col, pick_col]):
        return out

    sub = df[pd.to_numeric(df[season_col], errors="coerce") == season]
    for _, r in sub.iterrows():
        name = norm_name(r.get(name_col))
        if not name:
            continue
        out.setdefault(name, []).append(
            {
                "draft_capital_pick": numeric(r.get(pick_col)),
                "nfl_draft_round": numeric(r.get(round_col)) if round_col else None,
                "draft_position": norm_pos(r.get(pos_col)) if pos_col else None,
            }
        )
    return out


def combine_map(df, season):
    if df.empty:
        return {}
    season_col = first_col(df, "season", "draft_year")
    name_col = first_col(df, "full_name", "player_name", "name")
    pos_col = first_col(df, "pos", "position")
    if not all([season_col, name_col]):
        return {}

    sub = df[pd.to_numeric(df[season_col], errors="coerce") == season].copy()
    if sub.empty:
        return {}

    cols = {
        "forty": first_col(sub, "forty", "forty_yard", "forty_yard_dash"),
        "vertical": first_col(sub, "vertical", "vertical_jump"),
        "broad": first_col(sub, "broad_jump", "broad"),
        "shuttle": first_col(sub, "shuttle", "short_shuttle"),
        "cone": first_col(sub, "cone", "three_cone"),
    }

    sub["__position"] = sub[pos_col].map(norm_pos) if pos_col else "UNKNOWN"

    components = []
    for key, col in cols.items():
        if not col:
            continue
        raw = pd.to_numeric(sub[col], errors="coerce")
        dest = f"__{key}_pct"
        sub[dest] = pd.Series(np.nan, index=sub.index, dtype="float64")
        for pos in sub["__position"].fillna("UNKNOWN").unique():
            idx = sub.index[sub["__position"] == pos]
            # Lower is better for timed drills.
            higher = key not in {"forty", "shuttle", "cone"}
            sub.loc[idx, dest] = pct_rank(raw.loc[idx], higher)
        components.append(dest)

    if not components:
        return {}

    sub["__athleticism"] = sub[components].mean(axis=1, skipna=True)

    out = {}
    for _, r in sub.iterrows():
        n = norm_name(r.get(name_col))
        if not n:
            continue
        out.setdefault(n, []).append(
            {
                "athleticism": numeric(r.get("__athleticism")),
                "combine_position": r.get("__position"),
                "combine_measurements_used": int(
                    sum(pd.notna(r.get(c)) for c in components)
                ),
            }
        )
    return out


def unique_match(mapping, name, position=None, position_key=None):
    candidates = mapping.get(norm_name(name)) or []
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if position and position_key:
        same = [
            x for x in candidates
            if norm_pos(x.get(position_key)) == norm_pos(position)
        ]
        if len(same) == 1:
            return same[0]
    return None


def college_match(stats, name, position):
    if stats.empty:
        return None
    q = norm_name(name)
    rows = stats[stats["__norm_name"] == q]
    if rows.empty:
        return None
    if len(rows) == 1:
        return rows.iloc[0].to_dict()

    # If duplicated, prefer an explicit matching position.
    exact = rows[rows["__position"].map(norm_pos) == norm_pos(position)]
    if len(exact) == 1:
        return exact.iloc[0].to_dict()

    # Otherwise aggregate duplicates only if they represent same normalized name.
    numeric_cols = [
        c for c in rows.columns
        if pd.api.types.is_numeric_dtype(rows[c])
    ]
    result = rows.iloc[0].to_dict()
    for c in numeric_cols:
        result[c] = rows[c].sum(min_count=1)
    return result


def main():
    payload = load(INPUTS, {})
    prospects = payload.get("prospects") or []
    if not prospects:
        raise SystemExit("No prospect inputs found. Run build_gm30_prospect_inputs.py first.")

    season = int(payload.get("season"))
    college_season = season - 1

    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "FSFFL-GM-3.0-Prospect-Feature-Enrichment-v1",
        "season": season,
        "college_season": college_season,
        "sources": {},
        "matched": {"draft": 0, "combine": 0, "college": 0},
        "unmatched": {"draft": [], "combine": [], "college": []},
    }

    # Load sources independently. One source failure must not destroy all prospect data.
    try:
        draft_df = read_csv_url(DRAFT_URL)
        audit["sources"]["nflverse_draft"] = {"available": True, "url": DRAFT_URL}
    except Exception as e:
        draft_df = pd.DataFrame()
        audit["sources"]["nflverse_draft"] = {"available": False, "error": str(e)}

    try:
        combine_df = read_csv_url(COMBINE_URL)
        audit["sources"]["nflverse_combine"] = {"available": True, "url": COMBINE_URL}
    except Exception as e:
        combine_df = pd.DataFrame()
        audit["sources"]["nflverse_combine"] = {"available": False, "error": str(e)}

    cfb_url = CFB_PARQUET.format(season=college_season)
    try:
        college_raw = read_parquet_url(cfb_url)
        college = aggregate_player_stats(college_raw)
        college = add_team_shares(college)
        college = position_percentiles(
            college,
            [
                ("receiving_yards_share", "receiving_yards_share", True),
                ("target_share", "target_share", True),
                ("dominator", "dominator", True),
                ("yards_per_target", "receiving_efficiency", True),
                ("rushing_yards_share", "rushing_yards_share", True),
                ("scrimmage_yards_share", "scrimmage_yards_share", True),
                ("yards_per_carry", "rushing_efficiency", True),
                ("completion_pct", "completion_efficiency", True),
                ("yards_per_attempt", "passing_efficiency", True),
                ("sack_rate_inverse_raw", "sack_rate_inverse", True),
                ("td_int_efficiency_raw", "td_int_efficiency", True),
                ("rush_yards", "rushing_value", True),
            ],
        )
        audit["sources"]["cfbfastR_player_stats"] = {
            "available": True,
            "url": cfb_url,
            "raw_rows": int(len(college_raw)),
            "aggregated_players": int(len(college)),
        }
    except Exception as e:
        college = pd.DataFrame()
        audit["sources"]["cfbfastR_player_stats"] = {
            "available": False,
            "url": cfb_url,
            "error": str(e),
        }

    drafts = draft_map(draft_df, season)
    combines = combine_map(combine_df, season)

    enriched = []
    for row in prospects:
        row = dict(row)
        name = row.get("name")
        pos = norm_pos(row.get("position"))

        d = unique_match(drafts, name, pos, "draft_position")
        if d:
            row["draft_capital_pick"] = d.get("draft_capital_pick")
            row["nfl_draft_round"] = d.get("nfl_draft_round")
            row["draft_capital_source"] = "nflverse/PFR draft_picks"
            audit["matched"]["draft"] += 1
        else:
            audit["unmatched"]["draft"].append(name)

        c = unique_match(combines, name, pos, "combine_position")
        if c and c.get("athleticism") is not None:
            row["athleticism"] = round(float(c["athleticism"]), 6)
            row["combine_measurements_used"] = c.get("combine_measurements_used")
            row["athleticism_source"] = "nflverse/PFR combine"
            audit["matched"]["combine"] += 1
        else:
            audit["unmatched"]["combine"].append(name)

        college_row = college_match(college, name, pos)
        if college_row:
            mappings = {
                "receiving_yards_share": "receiving_yards_share",
                "target_share": "target_share",
                "dominator": "dominator",
                "receiving_efficiency": "receiving_efficiency",
                "rushing_yards_share": "rushing_yards_share",
                "scrimmage_yards_share": "scrimmage_yards_share",
                "rushing_efficiency": "rushing_efficiency",
                "completion_efficiency": "completion_efficiency",
                "passing_efficiency": "passing_efficiency",
                "sack_rate_inverse": "sack_rate_inverse",
                "td_int_efficiency": "td_int_efficiency",
                "rushing_value": "rushing_value",
            }
            used = []
            for src, dest in mappings.items():
                v = numeric(college_row.get(src))
                if v is not None:
                    row[dest] = round(float(v), 6)
                    used.append(dest)
            if used:
                row["college_features_source"] = (
                    f"SportsDataverse/cfbfastR player_stats {college_season}"
                )
                row["college_features_used"] = used
                audit["matched"]["college"] += 1
            else:
                audit["unmatched"]["college"].append(name)
        else:
            audit["unmatched"]["college"].append(name)

        enriched.append(row)

    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["feature_enrichment_model"] = audit["model_version"]
    payload["prospects"] = enriched
    save(INPUTS, payload)

    audit["prospect_count"] = len(enriched)
    audit["match_rates"] = {
        k: round(v / max(len(enriched), 1), 4)
        for k, v in audit["matched"].items()
    }
    save(AUDIT, audit)

    print(
        "GM 3.0 prospect enrichment: "
        f"{len(enriched)} prospects | "
        f"draft {audit['matched']['draft']} | "
        f"combine {audit['matched']['combine']} | "
        f"college {audit['matched']['college']}"
    )
    print(f"Audit -> {AUDIT}")


if __name__ == "__main__":
    main()
