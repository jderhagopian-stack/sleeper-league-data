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
3. Downloads the latest preseason/draft FantasyPros consensus feed.
4. Recognizes the draft feed's native schema (player/id/pos/tm/ecr).
5. Downloads cross-platform player ID mappings.
6. Maps both ranking sources to Sleeper IDs, with conservative name fallback.
7. Saves raw source snapshots for audit/history.
8. Writes normalized weekly + draft source tables and source-quality audit.
9. Inventories draft page types and FSFFL roster coverage before selecting a
   scoring-format projection source.
10. Selects a current-season preseason prior using redraft superflex rankings
    first, with redraft overall only as a coverage fallback. Dynasty rankings
    are deliberately excluded from the scoring prior.

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
DRAFT_URL = (
    "https://raw.githubusercontent.com/"
    "dynastyprocess/data/master/files/db_fpecr_latest.csv"
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


def normalize_rank_rows(
    rows: List[Dict[str, str]],
    fp_to_sleeper: Dict[str, str],
    name_index: Dict[str, List[str]],
    source_type: str,
    detected_week=None,
):
    normalized = []
    direct_matches = 0
    name_matches = 0
    ambiguous_name_matches = 0
    unmatched = 0

    for row in rows:
        fp_id = find_column(
            row,
            "fantasypros_id",
            "fp_id",
            "id",
        )
        name = find_column(
            row,
            "player_name",
            "name",
            "player",
        )
        pos = find_column(row, "pos", "position")
        team = find_column(row, "team", "tm")
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
            "source_type": source_type,
            "sleeper_id": sleeper_id,
            "fantasypros_id": str(fp_id).strip() if fp_id else None,
            "player_name": name,
            "position": pos,
            "team": team,
            "source_week": detected_week,
            "ecr": to_float(find_column(row, "ecr", "avg", "average")),
            "rank": to_float(find_column(row, "rank", "rk", "ecr")),
            "expert_rank_sd": to_float(find_column(row, "sd", "std.dev", "std_dev")),
            "best_rank": to_float(find_column(row, "best")),
            "worst_rank": to_float(find_column(row, "worst")),
            "pos_rank": find_column(row, "pos_rank", "pos rank"),
            "bye_week": to_int(find_column(row, "player_bye_week", "bye")),
            "opponent": find_column(row, "player_opponent", "opponent"),
            "start_sit_grade": find_column(row, "start_sit_grade"),
            "rank_to_points": to_float(find_column(row, "r2p_pts")),
            "ecr_type": find_column(row, "ecr_type"),
            "page_type": find_column(row, "page_type"),
            "rank_delta": to_float(find_column(row, "rank_delta")),
            "scrape_date": find_column(row, "scrape_date"),
            "match_method": match_method,
        })

    return normalized, {
        "fantasypros_id": direct_matches,
        "normalized_name": name_matches,
        "ambiguous_name": ambiguous_name_matches,
        "unmatched": unmatched,
    }


def build_draft_page_inventory(
    normalized_draft: List[Dict[str, Any]],
    rostered_ids,
    active_roster_ids,
):
    pages: Dict[str, Dict[str, Any]] = {}

    for row in normalized_draft:
        page_type = row.get("page_type") or "UNKNOWN"
        bucket = pages.setdefault(page_type, {
            "rows": 0,
            "mapped_rows": 0,
            "ecr_types": {},
            "positions": {},
            "rostered_ids": set(),
            "active_rostered_ids": set(),
            "top_examples": [],
        })

        bucket["rows"] += 1
        if row.get("sleeper_id"):
            bucket["mapped_rows"] += 1

        ecr_type = row.get("ecr_type") or "UNKNOWN"
        bucket["ecr_types"][ecr_type] = bucket["ecr_types"].get(ecr_type, 0) + 1

        pos = row.get("position") or "UNKNOWN"
        bucket["positions"][pos] = bucket["positions"].get(pos, 0) + 1

        sid = str(row["sleeper_id"]) if row.get("sleeper_id") else None
        if sid and sid in rostered_ids:
            bucket["rostered_ids"].add(sid)
        if sid and sid in active_roster_ids:
            bucket["active_rostered_ids"].add(sid)

        if len(bucket["top_examples"]) < 8:
            bucket["top_examples"].append({
                "name": row.get("player_name"),
                "position": row.get("position"),
                "team": row.get("team"),
                "ecr": row.get("ecr"),
                "rank": row.get("rank"),
            })

    output = []
    for page_type, bucket in pages.items():
        output.append({
            "page_type": page_type,
            "rows": bucket["rows"],
            "mapped_rows": bucket["mapped_rows"],
            "mapping_coverage": round(
                bucket["mapped_rows"] / max(1, bucket["rows"]), 5
            ),
            "ecr_types": bucket["ecr_types"],
            "positions": bucket["positions"],
            "fsffl_rostered_coverage": {
                "covered": len(bucket["rostered_ids"]),
                "total": len(rostered_ids),
                "coverage": round(
                    len(bucket["rostered_ids"]) / max(1, len(rostered_ids)), 5
                ),
            },
            "fsffl_active_coverage": {
                "covered": len(bucket["active_rostered_ids"]),
                "total": len(active_roster_ids),
                "coverage": round(
                    len(bucket["active_rostered_ids"]) / max(1, len(active_roster_ids)), 5
                ),
            },
            "top_examples": bucket["top_examples"],
        })

    output.sort(
        key=lambda x: (
            x["fsffl_active_coverage"]["coverage"],
            x["mapped_rows"],
            x["rows"],
        ),
        reverse=True,
    )
    return output


def select_preseason_prior(
    normalized_draft: List[Dict[str, Any]],
    rostered_ids,
):
    """
    Build the season-current preseason prior used by Simulator 1.0.

    Primary source: redraft-op (FantasyPros redraft superflex / overall-player
    ranking set, identified upstream by ecr_type=rsf).

    Fallback source: redraft-overall for players absent from redraft-op.

    Dynasty ranking sets are intentionally excluded from the scoring prior:
    dynasty market value is not the same thing as expected current-season
    fantasy production.
    """
    primary_type = "redraft-op"
    fallback_type = "redraft-overall"

    by_type: Dict[str, Dict[str, Dict[str, Any]]] = {
        primary_type: {},
        fallback_type: {},
    }

    for row in normalized_draft:
        page_type = row.get("page_type")
        sid = str(row["sleeper_id"]) if row.get("sleeper_id") else None
        if page_type not in by_type or not sid:
            continue

        # One row per Sleeper player per selected page type.
        # Keep the row with the best (lowest) ECR if duplicates exist.
        existing = by_type[page_type].get(sid)
        if existing is None:
            by_type[page_type][sid] = row
        else:
            old_ecr = existing.get("ecr")
            new_ecr = row.get("ecr")
            if (
                new_ecr is not None
                and (old_ecr is None or float(new_ecr) < float(old_ecr))
            ):
                by_type[page_type][sid] = row

    selected = {}
    source_counts = {
        primary_type: 0,
        fallback_type: 0,
        "missing": 0,
    }

    for sid in sorted(rostered_ids):
        row = by_type[primary_type].get(sid)
        chosen_source = primary_type

        if row is None:
            row = by_type[fallback_type].get(sid)
            chosen_source = fallback_type

        if row is None:
            source_counts["missing"] += 1
            continue

        selected[sid] = {
            "sleeper_id": sid,
            "player_name": row.get("player_name"),
            "position": row.get("position"),
            "team": row.get("team"),
            "preseason_ecr": row.get("ecr"),
            "expert_rank_sd": row.get("expert_rank_sd"),
            "best_rank": row.get("best_rank"),
            "worst_rank": row.get("worst_rank"),
            "bye_week": row.get("bye_week"),
            "source_page_type": chosen_source,
            "source_ecr_type": row.get("ecr_type"),
            "scrape_date": row.get("scrape_date"),
        }
        source_counts[chosen_source] += 1

    return selected, source_counts


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
    print("Downloading preseason/draft ffverse/DynastyProcess source...")
    draft_text = fetch_text(DRAFT_URL)
    print("Downloading player ID mapping...")
    ids_text = fetch_text(PLAYER_IDS_URL)

    # Preserve exact downloaded source files for reproducibility/backtesting.
    weekly_snapshot = sources_dir / f"fp_latest_weekly_{stamp}.csv"
    draft_snapshot = sources_dir / f"db_fpecr_latest_{stamp}.csv"
    ids_snapshot = sources_dir / f"db_playerids_{stamp}.csv"
    weekly_snapshot.write_text(weekly_text, encoding="utf-8")
    draft_snapshot.write_text(draft_text, encoding="utf-8")
    ids_snapshot.write_text(ids_text, encoding="utf-8")

    # Also maintain convenient latest aliases.
    (sources_dir / "fp_latest_weekly.csv").write_text(
        weekly_text, encoding="utf-8"
    )
    (sources_dir / "db_fpecr_latest.csv").write_text(
        draft_text, encoding="utf-8"
    )
    (sources_dir / "db_playerids.csv").write_text(
        ids_text, encoding="utf-8"
    )

    weekly_rows = parse_csv(weekly_text)
    draft_rows = parse_csv(draft_text)
    id_rows = parse_csv(ids_text)

    fp_to_sleeper = build_fp_to_sleeper(id_rows)
    name_index = sleeper_player_name_index(players)
    detected_week = detect_week(weekly_rows)

    normalized_weekly, weekly_methods = normalize_rank_rows(
        weekly_rows,
        fp_to_sleeper,
        name_index,
        source_type="weekly",
        detected_week=detected_week,
    )
    normalized_draft, draft_methods = normalize_rank_rows(
        draft_rows,
        fp_to_sleeper,
        name_index,
        source_type="draft",
        detected_week=None,
    )

    weekly_mapped = sum(1 for row in normalized_weekly if row["sleeper_id"])
    weekly_total = len(normalized_weekly)
    weekly_coverage = weekly_mapped / weekly_total if weekly_total else 0.0

    draft_mapped = sum(1 for row in normalized_draft if row["sleeper_id"])
    draft_total = len(normalized_draft)
    draft_coverage = draft_mapped / draft_total if draft_total else 0.0

    # Union of sources is what matters for simulator coverage.
    normalized = normalized_weekly + normalized_draft
    mapped = sum(1 for row in normalized if row["sleeper_id"])
    total = len(normalized)
    coverage = mapped / total if total else 0.0

    # FSFFL-specific roster coverage is more important than global source coverage.
    rosters = load_json(DATA / "rosters.json", [])
    rostered_ids = set()
    taxi_ids = set()
    reserve_ids = set()
    for roster in rosters or []:
        rostered_ids.update(str(x) for x in (roster.get("players") or []))
        taxi_ids.update(str(x) for x in (roster.get("taxi") or []))
        reserve_ids.update(str(x) for x in (roster.get("reserve") or []))

    mapped_sleeper_ids = {
        str(row["sleeper_id"])
        for row in normalized
        if row.get("sleeper_id")
    }

    rostered_covered = sorted(rostered_ids & mapped_sleeper_ids)
    rostered_missing = sorted(rostered_ids - mapped_sleeper_ids)
    active_roster_ids = rostered_ids - taxi_ids
    active_covered = sorted(active_roster_ids & mapped_sleeper_ids)
    active_missing = sorted(active_roster_ids - mapped_sleeper_ids)

    roster_coverage = len(rostered_covered) / max(1, len(rostered_ids))
    active_roster_coverage = len(active_covered) / max(1, len(active_roster_ids))

    draft_page_inventory = build_draft_page_inventory(
        normalized_draft,
        rostered_ids,
        active_roster_ids,
    )

    preseason_prior, preseason_prior_counts = select_preseason_prior(
        normalized_draft,
        rostered_ids,
    )

    missing_details = []
    for pid in rostered_missing:
        p = (players or {}).get(str(pid)) or {}
        full_name = p.get("full_name")
        if not full_name:
            full_name = f'{p.get("first_name") or ""} {p.get("last_name") or ""}'.strip()
        missing_details.append({
            "sleeper_id": str(pid),
            "name": full_name or str(pid),
            "position": p.get("position"),
            "team": p.get("team"),
            "on_taxi": str(pid) in taxi_ids,
            "on_reserve": str(pid) in reserve_ids,
        })

    source_payload = {
        "generated_at_utc": generated,
        "season": season,
        "sources": [
            {
                "name": "DynastyProcess / ffverse weekly fantasy rankings",
                "type": "weekly",
                "url": WEEKLY_URL,
                "source_week": detected_week,
                "rows": weekly_total,
                "mapped_rows": weekly_mapped,
                "mapping_coverage": round(weekly_coverage, 5),
            },
            {
                "name": "DynastyProcess / ffverse preseason draft rankings",
                "type": "draft",
                "url": DRAFT_URL,
                "rows": draft_total,
                "mapped_rows": draft_mapped,
                "mapping_coverage": round(draft_coverage, 5),
            },
        ],
        "player_ids_url": PLAYER_IDS_URL,
        "important_note": (
            "These are normalized ranking/projection source layers, not the final "
            "player weekly projection feed. Weekly and draft consensus are retained "
            "as distinct inputs for later blending and calibration."
        ),
        "players": normalized,
    }

    audit = {
        "generated_at_utc": generated,
        "season": season,
        "source_rows": total,
        "mapped_rows": mapped,
        "mapping_coverage": round(coverage, 5),
        "weekly_source": {
            "rows": weekly_total,
            "mapped_rows": weekly_mapped,
            "mapping_coverage": round(weekly_coverage, 5),
            "mapping_methods": weekly_methods,
        },
        "draft_source": {
            "rows": draft_total,
            "mapped_rows": draft_mapped,
            "mapping_coverage": round(draft_coverage, 5),
            "mapping_methods": draft_methods,
        },
        "detected_source_week": detected_week,
        "fsffl_roster_coverage": {
            "all_rostered_players": {
                "total": len(rostered_ids),
                "covered": len(rostered_covered),
                "missing": len(rostered_missing),
                "coverage": round(roster_coverage, 5),
            },
            "active_non_taxi_players": {
                "total": len(active_roster_ids),
                "covered": len(active_covered),
                "missing": len(active_missing),
                "coverage": round(active_roster_coverage, 5),
            },
            "missing_players": missing_details,
        },
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

    if roster_coverage < 0.95:
        audit["quality_flags"].append({
            "severity": "warning",
            "code": "LOW_FSFFL_ROSTER_COVERAGE",
            "message": (
                f"Only {roster_coverage:.1%} of currently rostered FSFFL players "
                "are present in the weekly source."
            ),
        })

    if active_roster_coverage < 0.95:
        audit["quality_flags"].append({
            "severity": "warning",
            "code": "LOW_FSFFL_ACTIVE_ROSTER_COVERAGE",
            "message": (
                f"Only {active_roster_coverage:.1%} of non-taxi FSFFL players "
                "are present in the weekly source."
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

    total_name_matches = (
        weekly_methods.get("normalized_name", 0)
        + draft_methods.get("normalized_name", 0)
    )
    if total_name_matches:
        audit["quality_flags"].append({
            "severity": "info",
            "code": "NAME_FALLBACK_USED",
            "message": (
                f"{total_name_matches} rows required normalized-name fallback "
                "across weekly + draft sources."
            ),
        })

    write_json(
        sources_dir / "normalized_rankings_combined.json",
        source_payload,
    )
    write_json(
        sources_dir / "normalized_weekly_rankings.json",
        {
            "generated_at_utc": generated,
            "season": season,
            "source": "weekly",
            "source_week": detected_week,
            "players": normalized_weekly,
        },
    )
    write_json(
        sources_dir / "normalized_draft_rankings.json",
        {
            "generated_at_utc": generated,
            "season": season,
            "source": "draft",
            "players": normalized_draft,
        },
    )
    write_json(
        outputs_dir / "draft_page_inventory.json",
        {
            "generated_at_utc": generated,
            "season": season,
            "purpose": (
                "Inventory the preseason/draft feed by page_type so Simulator "
                "1.0 can select the scoring format that best matches FSFFL "
                "instead of mixing best-ball, standard, PPR, superflex, or "
                "position-specific ranking sets."
            ),
            "pages": draft_page_inventory,
        },
    )
    write_json(
        sources_dir / "selected_preseason_prior.json",
        {
            "generated_at_utc": generated,
            "season": season,
            "purpose": (
                "Current-season preseason prior for Simulator 1.0. "
                "Uses redraft superflex rankings first and redraft overall "
                "only as a coverage fallback. Dynasty rankings are excluded "
                "from the scoring prior."
            ),
            "selection_policy": {
                "primary_page_type": "redraft-op",
                "primary_ecr_type": "rsf",
                "fallback_page_type": "redraft-overall",
                "excluded_from_scoring_prior": [
                    "dynasty-op",
                    "dynasty-overall",
                    "best-overall",
                    "position-only dynasty pages",
                ],
            },
            "source_counts": preseason_prior_counts,
            "players": preseason_prior,
        },
    )
    write_json(
        outputs_dir / "projection_source_audit.json",
        audit,
    )

    print(
        f"Projection source build complete for {season}: "
        f"weekly {weekly_mapped}/{weekly_total} ({weekly_coverage:.1%}), "
        f"draft {draft_mapped}/{draft_total} ({draft_coverage:.1%})."
    )
    print(
        "No final player_weekly_projections.json was overwritten. "
        "That file will be generated only after the projection blend is ready."
    )


if __name__ == "__main__":
    main()
