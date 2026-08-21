#!/usr/bin/env python3
"""
FSFFL player projection source builder.

Phase 1 purpose
---------------
This script creates a clean, season-aware projection-source layer for the
FSFFL Season Simulator. It does NOT yet pretend that expert rank or market
value is a complete weekly fantasy-point projection.

It currently:
1. Detects the active season from data/league.json.
2. Downloads the latest open weekly fantasy ranking/projection feed from
   DynastyProcess / ffverse.
3. Downloads cross-platform player ID mappings.
4. Maps those players to Sleeper IDs, with conservative name fallback.
5. Saves raw source snapshots for audit/history.
6. Writes a normalized weekly source table and source-quality audit.

Later projection layers can blend this source with additional season
projections, usage, expected points, injury availability, schedule, and
historical distributions before writing the final simulator input:
data/simulator/<season>/inputs/player_weekly_projections.json
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA = Path("data")
SIM_ROOT = DATA / "simulator"

WEEKLY_URL = (
    "https://raw.githubusercontent.com/"
    "dynastyprocess/data/master/files/fp_latest_weekly.csv"
)
PLAYER_IDS_URL = (
    "https://raw.githubusercontent.com/"
    "dynastyprocess/data/master/files/db_playerids.csv"
)

USER_AGENT = "FSFFL-Season-Simulator/1.0"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def fetch_text(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8-sig")


def parse_csv(text: str) -> List[Dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def norm_text(value: Optional[str]) -> str:
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def to_float(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def sleeper_player_name_index(players: Dict[str, Any]):
    by_name = {}
    for pid, row in (players or {}).items():
        full_name = row.get("full_name")
        if not full_name:
            first = row.get("first_name") or ""
            last = row.get("last_name") or ""
            full_name = f"{first} {last}".strip()
        key = norm_text(full_name)
        if not key:
            continue
        by_name.setdefault(key, []).append(str(pid))
    return by_name


def find_column(row: Dict[str, str], *names: str):
    lowered = {str(k).lower(): k for k in row.keys()}
    for name in names:
        key = lowered.get(name.lower())
        if key is not None:
            return row.get(key)
    return None


def build_fp_to_sleeper(id_rows: List[Dict[str, str]]):
    mapping = {}
    for row in id_rows:
        fp_id = find_column(
            row,
            "fantasypros_id",
            "fp_id",
            "fantasypros",
        )
        sleeper_id = find_column(
            row,
            "sleeper_id",
            "sleeper",
        )
        if fp_id and sleeper_id:
            mapping[str(fp_id).strip()] = str(sleeper_id).strip()
    return mapping


def detect_week(rows: List[Dict[str, str]]):
    """
    The public weekly feed is a current-week snapshot and may not always
    expose a literal week column. We preserve any explicit week field if
    present; otherwise the source record remains week=None rather than
    inventing a week number.
    """
    candidates = []
    for row in rows[:50]:
        week = find_column(row, "week", "scoring_week", "fantasy_week")
        week = to_int(week)
        if week is not None:
            candidates.append(week)
    if candidates:
        return max(set(candidates), key=candidates.count)
    return None


def main():
    league = load_json(DATA / "league.json")
    players = load_json(DATA / "players.json", {})

    if not league:
        raise RuntimeError("Missing data/league.json")
    if not players:
        raise RuntimeError("Missing data/players.json")

    season = str(league.get("season"))
    if not season:
        raise RuntimeError("League season is missing.")

    season_dir = SIM_ROOT / season
    sources_dir = season_dir / "sources"
    outputs_dir = season_dir / "outputs"
    sources_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    generated = now_utc()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("Downloading weekly ffverse/DynastyProcess source...")
    weekly_text = fetch_text(WEEKLY_URL)
    print("Downloading player ID mapping...")
    ids_text = fetch_text(PLAYER_IDS_URL)

    # Preserve exact downloaded source files for reproducibility/backtesting.
    weekly_snapshot = sources_dir / f"fp_latest_weekly_{stamp}.csv"
    ids_snapshot = sources_dir / f"db_playerids_{stamp}.csv"
    weekly_snapshot.write_text(weekly_text, encoding="utf-8")
    ids_snapshot.write_text(ids_text, encoding="utf-8")

    # Also maintain convenient latest aliases.
    (sources_dir / "fp_latest_weekly.csv").write_text(
        weekly_text, encoding="utf-8"
    )
    (sources_dir / "db_playerids.csv").write_text(
        ids_text, encoding="utf-8"
    )

    weekly_rows = parse_csv(weekly_text)
    id_rows = parse_csv(ids_text)

    fp_to_sleeper = build_fp_to_sleeper(id_rows)
    name_index = sleeper_player_name_index(players)
    detected_week = detect_week(weekly_rows)

    normalized = []
    direct_matches = 0
    name_matches = 0
    ambiguous_name_matches = 0
    unmatched = 0

    for row in weekly_rows:
        fp_id = find_column(row, "fantasypros_id", "fp_id")
        name = find_column(row, "player_name", "name")
        pos = find_column(row, "pos", "position")
        team = find_column(row, "team")
        sleeper_id = None
        match_method = None

        if fp_id:
            sleeper_id = fp_to_sleeper.get(str(fp_id).strip())
            if sleeper_id:
                direct_matches += 1
                match_method = "fantasypros_id"

        if not sleeper_id and name:
            matches = name_index.get(norm_text(name), [])
            if len(matches) == 1:
                sleeper_id = matches[0]
                name_matches += 1
                match_method = "normalized_name"
            elif len(matches) > 1:
                ambiguous_name_matches += 1
                match_method = "ambiguous_name"
            else:
                unmatched += 1
                match_method = "unmatched"

        normalized.append({
            "sleeper_id": sleeper_id,
            "fantasypros_id": str(fp_id).strip() if fp_id else None,
            "player_name": name,
            "position": pos,
            "team": team,
            "source_week": detected_week,
            "ecr": to_float(find_column(row, "ecr")),
            "rank": to_float(find_column(row, "rank")),
            "expert_rank_sd": to_float(find_column(row, "sd")),
            "best_rank": to_float(find_column(row, "best")),
            "worst_rank": to_float(find_column(row, "worst")),
            "pos_rank": find_column(row, "pos_rank"),
            "bye_week": to_int(find_column(row, "player_bye_week")),
            "opponent": find_column(row, "player_opponent"),
            "start_sit_grade": find_column(row, "start_sit_grade"),
            # r2p_pts is retained exactly as a source field, but is NOT
            # automatically promoted to our final fantasy-point mean.
            "rank_to_points": to_float(find_column(row, "r2p_pts")),
            "match_method": match_method,
        })

    mapped = sum(1 for row in normalized if row["sleeper_id"])
    total = len(normalized)
    coverage = mapped / total if total else 0.0

    source_payload = {
        "generated_at_utc": generated,
        "season": season,
        "source": {
            "name": "DynastyProcess / ffverse weekly fantasy rankings",
            "url": WEEKLY_URL,
            "player_ids_url": PLAYER_IDS_URL,
            "source_week": detected_week,
        },
        "important_note": (
            "This is a normalized source layer, not the final player weekly "
            "projection feed. ECR/rank/rank_to_points are retained as inputs "
            "for later blending and calibration."
        ),
        "players": normalized,
    }

    audit = {
        "generated_at_utc": generated,
        "season": season,
        "source_rows": total,
        "mapped_rows": mapped,
        "mapping_coverage": round(coverage, 5),
        "mapping_methods": {
            "fantasypros_id": direct_matches,
            "normalized_name": name_matches,
            "ambiguous_name": ambiguous_name_matches,
            "unmatched": unmatched,
        },
        "detected_source_week": detected_week,
        "quality_flags": [],
    }

    if coverage < 0.90:
        audit["quality_flags"].append({
            "severity": "warning",
            "code": "LOW_MAPPING_COVERAGE",
            "message": (
                f"Only {coverage:.1%} of weekly source rows mapped to Sleeper."
            ),
        })

    if detected_week is None:
        audit["quality_flags"].append({
            "severity": "info",
            "code": "NO_EXPLICIT_WEEK_COLUMN",
            "message": (
                "The current weekly source snapshot does not expose an explicit "
                "week column. No week number was invented."
            ),
        })

    if name_matches:
        audit["quality_flags"].append({
            "severity": "info",
            "code": "NAME_FALLBACK_USED",
            "message": (
                f"{name_matches} rows required normalized-name fallback. "
                "This is expected for some players, especially where upstream "
                "FantasyPros IDs are incomplete."
            ),
        })

    write_json(
        sources_dir / "normalized_weekly_rankings.json",
        source_payload,
    )
    write_json(
        outputs_dir / "projection_source_audit.json",
        audit,
    )

    print(
        f"Projection source build complete for {season}: "
        f"{mapped}/{total} rows mapped to Sleeper ({coverage:.1%})."
    )
    print(
        "No final player_weekly_projections.json was overwritten. "
        "That file will be generated only after the projection blend is ready."
    )


if __name__ == "__main__":
    main()
