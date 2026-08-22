#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Prospect Feature Enrichment v1

Reliable automated sources
--------------------------
1) Wikipedia MediaWiki API current NFL draft table:
   https://en.wikipedia.org/w/api.php?action=parse&page=<season>_NFL_draft&prop=wikitext
2) nflverse combine dataset (PFR-backed):
   https://github.com/nflverse/nflverse-data/releases/download/combine/combine.csv
3) SportsDataverse season-summary releases:
   espn_cfb_receiving / espn_cfb_rushing / espn_cfb_passing

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
import html
import unicodedata
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data")
INPUTS = DATA / "gm3_prospect_inputs.json"
AUDIT = DATA / "gm3_prospect_feature_audit.json"

WIKIPEDIA_DRAFT_API = (
    "https://en.wikipedia.org/w/api.php?action=parse&page={season}_NFL_draft"
    "&prop=wikitext&format=json&formatversion=2"
)
COMBINE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "combine/combine.csv"
)
SPORTSDATAVERSE_RELEASE_API = (
    "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/"
    "releases/tags/{tag}"
)
COLLEGE_RELEASE_TAGS = {
    "receiving": "espn_cfb_receiving",
    "rushing": "espn_cfb_rushing",
    "passing": "espn_cfb_passing",
}

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


def fetch_bytes(url, timeout=90, accept=None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; FSFFL-GM30/1.0; "
            "+https://github.com/jderhagopian-stack/sleeper-league-data)"
        )
    }
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_text(url, timeout=90):
    return fetch_bytes(url, timeout=timeout).decode("utf-8", errors="replace")


def strip_html(value):
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    return html.unescape(value).strip()


def read_wikipedia_draft(season):
    """
    Load the complete NFL draft from Wikipedia's MediaWiki API.

    The NFL draft article stores each selection as an NFLDraft-row template:
      {{NFLDraft-row |draftyear=2026 |round=1 |picknum=1
        |first=Fernando |last=Mendoza |position=QB ... }}

    We parse only explicit template parameters (round, overall pick, first,
    last, position). Notes/trades/markup are ignored.

    This avoids scraping rendered HTML and avoids Pro-Football-Reference's
    automated-request blocking in GitHub Actions.
    """
    url = WIKIPEDIA_DRAFT_API.format(season=season)
    payload = json.loads(
        fetch_bytes(
            url,
            accept="application/json",
        ).decode("utf-8")
    )

    parse_obj = payload.get("parse") or {}
    wikitext_obj = parse_obj.get("wikitext")
    if isinstance(wikitext_obj, dict):
        text = wikitext_obj.get("*") or ""
    else:
        text = wikitext_obj or ""

    if not text:
        raise RuntimeError(
            f"Wikipedia MediaWiki API returned no wikitext for {season} NFL draft"
        )

    # Splitting on the template start is safer than matching balanced braces
    # because note fields can contain nested templates/refn markup.
    chunks = text.split("{{NFLDraft-row")
    rows = []

    def field(chunk, key):
        m = re.search(
            rf"\|{re.escape(key)}\s*=\s*([^|\n}}]+)",
            chunk,
            flags=re.I,
        )
        return strip_html(m.group(1)).strip() if m else None

    for chunk in chunks[1:]:
        # Stop before the next unrelated major template if present. Field regex
        # reads only pipe parameters and is not affected by notes after them.
        round_raw = field(chunk, "round")
        pick_raw = field(chunk, "picknum")
        first = field(chunk, "first")
        last = field(chunk, "last")
        pos = field(chunk, "position")
        draftyear = field(chunk, "draftyear")

        try:
            rnd = int(str(round_raw).strip())
            pick = int(str(pick_raw).strip())
            yr = int(str(draftyear or season).strip())
        except (TypeError, ValueError):
            continue

        if yr != int(season) or not (1 <= pick <= 300):
            continue

        # Remove simple wiki links/templates that may appear in name parameters.
        def clean_wiki_name(x):
            x = str(x or "").strip()
            x = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", x)
            x = re.sub(r"\{\{[^{}]*\}\}", "", x)
            x = re.sub(r"<[^>]+>", "", x)
            return html.unescape(x).strip()

        first = clean_wiki_name(first)
        last = clean_wiki_name(last)
        name = " ".join(x for x in (first, last) if x).strip()
        if not name:
            continue

        rows.append(
            {
                "season": int(season),
                "full_name": name,
                "position": norm_pos(pos),
                "pick": pick,
                "round": rnd,
            }
        )

    # Full modern drafts should have ~250 selections. Keep threshold lower to
    # tolerate unusual forfeitures while still failing loudly on a partial page.
    if len(rows) < 200:
        raise RuntimeError(
            f"Wikipedia draft parse returned only {len(rows)} selections for {season}"
        )

    # Overall pick should be unique. De-duplicate defensively by overall pick.
    df = pd.DataFrame(rows)
    df = df.sort_values("pick").drop_duplicates("pick", keep="first").reset_index(drop=True)

    if len(df) < 200:
        raise RuntimeError(
            f"Wikipedia draft de-duplication left only {len(df)} selections for {season}"
        )

    return df, url


def release_asset_url(tag, season):
    """
    Discover the exact season parquet asset through the GitHub release API.
    This avoids hard-coding SportsDataverse asset filenames.
    """
    api_url = SPORTSDATAVERSE_RELEASE_API.format(tag=tag)
    payload = json.loads(
        fetch_bytes(
            api_url,
            accept="application/vnd.github+json",
        ).decode("utf-8")
    )

    assets = payload.get("assets") or []
    parquet = []
    for asset in assets:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".parquet") and str(season) in name:
            parquet.append(asset)

    if not parquet:
        raise RuntimeError(
            f"No {season} parquet asset found in SportsDataverse release {tag}"
        )

    # Prefer exact/simple season asset names when several candidates exist.
    parquet.sort(
        key=lambda a: (
            len(str(a.get("name") or "")),
            str(a.get("name") or ""),
        )
    )
    chosen = parquet[0]
    url = chosen.get("browser_download_url")
    if not url:
        raise RuntimeError(f"Release asset for {tag} lacks browser_download_url")

    return url, str(chosen.get("name") or "")


def read_release_parquet(tag, season):
    url, asset_name = release_asset_url(tag, season)
    raw = fetch_bytes(url, timeout=120)
    return pd.read_parquet(io.BytesIO(raw)), url, asset_name


def aggregate_college_summaries(receiving, rushing, passing):
    """
    Convert SportsDataverse season-summary tables into one normalized player
    feature frame. Team shares are calculated before player-level aggregation.
    """
    frames = []

    # RECEIVING ---------------------------------------------------------------
    if isinstance(receiving, pd.DataFrame) and not receiving.empty:
        r = receiving.copy()
        name_col = first_col(r, "receiver_player_name")
        team_col = first_col(r, "pos_team")
        yards_col = first_col(r, "yards")
        targets_col = first_col(r, "targets", "plays")
        rec_col = first_col(r, "comp")
        td_col = first_col(r, "passing_td")
        epa_col = first_col(r, "EPAplay")
        success_col = first_col(r, "success")

        if name_col and team_col:
            r["__name"] = r[name_col]
            r["__norm_name"] = r["__name"].map(norm_name)
            r["__team"] = r[team_col]

            for dest, col in (
                ("rec_yards", yards_col),
                ("targets", targets_col),
                ("receptions", rec_col),
                ("rec_td", td_col),
                ("rec_epa_play", epa_col),
                ("rec_success", success_col),
            ):
                r[dest] = pd.to_numeric(r[col], errors="coerce") if col else np.nan

            team_yards = r.groupby("__team")["rec_yards"].transform("sum")
            team_targets = r.groupby("__team")["targets"].transform("sum")
            team_td = r.groupby("__team")["rec_td"].transform("sum")

            r["receiving_yards_share_raw"] = (
                r["rec_yards"] / team_yards.replace(0, np.nan)
            )
            r["target_share_raw"] = (
                r["targets"] / team_targets.replace(0, np.nan)
            )
            r["receiving_td_share_raw"] = (
                r["rec_td"] / team_td.replace(0, np.nan)
            )
            r["dominator_raw"] = r[
                ["receiving_yards_share_raw", "receiving_td_share_raw"]
            ].mean(axis=1, skipna=True)

            # Efficiency composite: EPA/play + success, falling back to yards/target.
            ypt = r["rec_yards"] / r["targets"].replace(0, np.nan)
            r["receiving_efficiency_raw"] = (
                r[["rec_epa_play", "rec_success"]]
                .mean(axis=1, skipna=True)
            )
            r.loc[
                r["receiving_efficiency_raw"].isna(),
                "receiving_efficiency_raw",
            ] = ypt

            frames.append(
                r[
                    [
                        "__norm_name", "__name",
                        "receiving_yards_share_raw",
                        "target_share_raw",
                        "dominator_raw",
                        "receiving_efficiency_raw",
                    ]
                ]
            )

    # RUSHING -----------------------------------------------------------------
    if isinstance(rushing, pd.DataFrame) and not rushing.empty:
        r = rushing.copy()
        name_col = first_col(r, "rusher_player_name")
        team_col = first_col(r, "pos_team")
        yards_col = first_col(r, "yards")
        carries_col = first_col(r, "plays")
        td_col = first_col(r, "rushing_td")
        epa_col = first_col(r, "EPAplay")
        success_col = first_col(r, "success")

        if name_col and team_col:
            r["__name"] = r[name_col]
            r["__norm_name"] = r["__name"].map(norm_name)
            r["__team"] = r[team_col]
            for dest, col in (
                ("rush_yards", yards_col),
                ("rush_attempts", carries_col),
                ("rush_td", td_col),
                ("rush_epa_play", epa_col),
                ("rush_success", success_col),
            ):
                r[dest] = pd.to_numeric(r[col], errors="coerce") if col else np.nan

            team_yards = r.groupby("__team")["rush_yards"].transform("sum")
            r["rushing_yards_share_raw"] = (
                r["rush_yards"] / team_yards.replace(0, np.nan)
            )
            ypc = r["rush_yards"] / r["rush_attempts"].replace(0, np.nan)
            r["rushing_efficiency_raw"] = (
                r[["rush_epa_play", "rush_success"]]
                .mean(axis=1, skipna=True)
            )
            r.loc[
                r["rushing_efficiency_raw"].isna(),
                "rushing_efficiency_raw",
            ] = ypc
            r["rushing_value_raw"] = r["rush_yards"]

            frames.append(
                r[
                    [
                        "__norm_name", "__name",
                        "rushing_yards_share_raw",
                        "rushing_efficiency_raw",
                        "rushing_value_raw",
                    ]
                ]
            )

    # PASSING -----------------------------------------------------------------
    if isinstance(passing, pd.DataFrame) and not passing.empty:
        p = passing.copy()
        name_col = first_col(p, "passer_player_name")
        if name_col:
            p["__name"] = p[name_col]
            p["__norm_name"] = p["__name"].map(norm_name)

            fields = {
                "pass_yards": first_col(p, "yards"),
                "pass_att": first_col(p, "att"),
                "pass_comp": first_col(p, "comp"),
                "pass_td": first_col(p, "passing_td"),
                "pass_int": first_col(p, "pass_int"),
                "sacked": first_col(p, "sacked"),
                "pass_epa_play": first_col(p, "EPAplay"),
                "pass_success": first_col(p, "success"),
                "yardsdropback": first_col(p, "yardsdropback"),
                "comppct": first_col(p, "comppct"),
            }
            for dest, col in fields.items():
                p[dest] = pd.to_numeric(p[col], errors="coerce") if col else np.nan

            p["passing_efficiency_raw"] = (
                p[["pass_epa_play", "pass_success", "yardsdropback"]]
                .mean(axis=1, skipna=True)
            )
            p["completion_efficiency_raw"] = p["comppct"]
            dropbacks = p["pass_att"].fillna(0) + p["sacked"].fillna(0)
            p["sack_rate_inverse_raw"] = 1.0 - (
                p["sacked"].fillna(0) / dropbacks.replace(0, np.nan)
            )
            td = p["pass_td"].fillna(0)
            ints = p["pass_int"].fillna(0)
            p["td_int_efficiency_raw"] = (
                (td + 1.0) / (td + ints + 2.0)
            )

            frames.append(
                p[
                    [
                        "__norm_name", "__name",
                        "passing_efficiency_raw",
                        "completion_efficiency_raw",
                        "sack_rate_inverse_raw",
                        "td_int_efficiency_raw",
                    ]
                ]
            )

    if not frames:
        return pd.DataFrame()

    # Outer merge all category frames by normalized name.
    merged = None
    for frame in frames:
        # Multiple-team seasons: aggregate raw feature columns by max for shares/
        # rates (best demonstrated stint) and sum only volume where applicable.
        feature_cols = [
            c for c in frame.columns if c not in {"__norm_name", "__name"}
        ]
        agg = {c: "max" for c in feature_cols}
        agg["__name"] = "first"
        frame = frame.groupby("__norm_name", as_index=False).agg(agg)

        if merged is None:
            merged = frame
        else:
            merged = merged.merge(
                frame,
                on="__norm_name",
                how="outer",
                suffixes=("", "__dup"),
            )
            if "__name__dup" in merged:
                merged["__name"] = merged["__name"].fillna(
                    merged["__name__dup"]
                )
                merged = merged.drop(columns=["__name__dup"])

    # Normalize raw metrics to 0..1 empirical percentiles.
    percentile_fields = [
        ("receiving_yards_share_raw", "receiving_yards_share"),
        ("target_share_raw", "target_share"),
        ("dominator_raw", "dominator"),
        ("receiving_efficiency_raw", "receiving_efficiency"),
        ("rushing_yards_share_raw", "rushing_yards_share"),
        ("rushing_efficiency_raw", "rushing_efficiency"),
        ("rushing_value_raw", "rushing_value"),
        ("passing_efficiency_raw", "passing_efficiency"),
        ("completion_efficiency_raw", "completion_efficiency"),
        ("sack_rate_inverse_raw", "sack_rate_inverse"),
        ("td_int_efficiency_raw", "td_int_efficiency"),
    ]
    for raw_col, out_col in percentile_fields:
        if raw_col in merged:
            merged[out_col] = pct_rank(merged[raw_col], True)
        else:
            merged[out_col] = np.nan

    # Approximate all-purpose scrimmage share from available receiving/rushing
    # team-share components. This remains transparent and source-derived.
    merged["scrimmage_yards_share"] = merged[
        ["receiving_yards_share", "rushing_yards_share"]
    ].max(axis=1, skipna=True)

    return merged


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
    name_col = first_col(df, "full_name", "name", "player")
    pos_col = first_col(df, "position", "pos", "category")
    pick_col = first_col(df, "pick", "overall", "draft_pick")
    round_col = first_col(df, "round", "draft_round")

    out = {}
    if not all([name_col, pick_col]):
        return out

    sub = df.copy()
    if season_col:
        sub = sub[
            pd.to_numeric(sub[season_col], errors="coerce") == int(season)
        ]

    for _, r in sub.iterrows():
        name = norm_name(r.get(name_col))
        if not name:
            continue
        out.setdefault(name, []).append(
            {
                "draft_capital_pick": numeric(r.get(pick_col)),
                "nfl_draft_round": (
                    numeric(r.get(round_col)) if round_col else None
                ),
                "draft_position": (
                    norm_pos(r.get(pos_col)) if pos_col else None
                ),
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
    # aggregate_college_summaries already de-duplicates normalized names.
    return rows.iloc[0].to_dict()


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
        draft_df, draft_url = read_wikipedia_draft(season)
        audit["sources"]["wikipedia_draft_api"] = {
            "available": True,
            "url": draft_url,
            "rows": int(len(draft_df)),
            "provenance": (
                "Wikipedia NFL draft selection table; article states selections "
                "are listed according to the NFL official draft tracker."
            ),
        }
    except Exception as e:
        draft_df = pd.DataFrame()
        audit["sources"]["wikipedia_draft_api"] = {
            "available": False,
            "url": WIKIPEDIA_DRAFT_API.format(season=season),
            "error": str(e),
        }

    try:
        combine_df = read_csv_url(COMBINE_URL)
        audit["sources"]["nflverse_combine"] = {
            "available": True,
            "url": COMBINE_URL,
        }
    except Exception as e:
        combine_df = pd.DataFrame()
        audit["sources"]["nflverse_combine"] = {
            "available": False,
            "error": str(e),
        }

    college_tables = {}
    college_source_meta = {}
    for kind, tag in COLLEGE_RELEASE_TAGS.items():
        try:
            df, url, asset_name = read_release_parquet(tag, college_season)
            college_tables[kind] = df
            college_source_meta[kind] = {
                "available": True,
                "tag": tag,
                "asset": asset_name,
                "url": url,
                "rows": int(len(df)),
            }
        except Exception as e:
            college_tables[kind] = pd.DataFrame()
            college_source_meta[kind] = {
                "available": False,
                "tag": tag,
                "error": str(e),
            }

    try:
        college = aggregate_college_summaries(
            college_tables.get("receiving", pd.DataFrame()),
            college_tables.get("rushing", pd.DataFrame()),
            college_tables.get("passing", pd.DataFrame()),
        )
        audit["sources"]["sportsdataverse_season_summaries"] = {
            "available": not college.empty,
            "college_season": college_season,
            "datasets": college_source_meta,
            "aggregated_players": int(len(college)),
        }
    except Exception as e:
        college = pd.DataFrame()
        audit["sources"]["sportsdataverse_season_summaries"] = {
            "available": False,
            "college_season": college_season,
            "datasets": college_source_meta,
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
            row["draft_capital_source"] = "Wikipedia MediaWiki NFL draft table (NFL tracker-referenced)"
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
                    f"SportsDataverse season summaries (passing/rushing/receiving) {college_season}"
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
