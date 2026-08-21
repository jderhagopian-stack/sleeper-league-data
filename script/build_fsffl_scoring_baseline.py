#!/usr/bin/env python3
"""
Build current-season FSFFL fantasy-point baselines from raw projected stats.

This is intentionally separate from the ranking-source builder.

What it does
------------
1. Detects the active season and exact scoring rules from data/league.json.
2. Downloads current-season FantasyPros consensus stat projections for
   QB/RB/WR/TE.
3. Parses the raw projected stats (not FantasyPros fantasy-point totals).
4. Maps projected players to Sleeper IDs using the already-selected
   preseason prior.
5. Recalculates fantasy points using FSFFL's actual scoring settings.
6. Writes a coverage/quality audit.
7. Does NOT yet create player_weekly_projections.json or run the simulator.

Why this matters
----------------
Ranking/ECR is useful as a prior, but Simulator 1.0 needs scoring expectations.
Using raw projected stats lets us score players under FSFFL's exact settings
instead of assuming another site's default scoring system.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA = Path("data")
SIM_ROOT = DATA / "simulator"

USER_AGENT = "FSFFL-Season-Simulator/1.0"

FP_URL = (
    "https://www.fantasypros.com/nfl/projections/{position}.php?week=draft"
)

POSITIONS = ("qb", "rb", "wr", "te")

TEAM_ALIASES = {
    "JAC": "JAX",
    "JAX": "JAX",
    "NEP": "NE",
    "NE": "NE",
    "KCC": "KC",
    "KC": "KC",
    "SFO": "SF",
    "SF": "SF",
    "GBP": "GB",
    "GB": "GB",
    "NOS": "NO",
    "NO": "NO",
    "TBB": "TB",
    "TB": "TB",
    "LVR": "LV",
    "LV": "LV",
}


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
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.upper() in {"NA", "N/A", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = value.replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def norm_team(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).upper().strip()
    return TEAM_ALIASES.get(value, value)


class ProjectionTableParser(HTMLParser):
    """Small stdlib HTML table parser; no pandas/bs4 dependency."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.in_target_table = False
        self.in_row = False
        self.in_cell = False
        self.current_cell: List[str] = []
        self.current_row: List[str] = []
        self.rows: List[List[str]] = []
        self.all_text: List[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == "table":
            if self.in_target_table:
                self.table_depth += 1
            elif attrs_dict.get("id") == "data":
                self.in_target_table = True
                self.table_depth = 1
            return

        if not self.in_target_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self.in_target_table:
            self.table_depth -= 1
            if self.table_depth <= 0:
                self.in_target_table = False
                self.table_depth = 0
            return

        if not self.in_target_table:
            return

        if tag in {"td", "th"} and self.in_cell:
            text = " ".join("".join(self.current_cell).split())
            self.current_row.append(text)
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
            self.in_row = False

    def handle_data(self, data):
        text = html.unescape(data)
        self.all_text.append(text)
        if self.in_target_table and self.in_cell:
            self.current_cell.append(text)


def split_player_team(cell: str) -> Tuple[str, Optional[str]]:
    text = " ".join(cell.split())
    match = re.match(r"^(.*?)\s+([A-Z]{2,3})$", text)
    if match:
        return match.group(1).strip(), norm_team(match.group(2))
    return text, None


def parse_projection_page(position: str, raw_html: str) -> List[Dict[str, Any]]:
    parser = ProjectionTableParser()
    parser.feed(raw_html)

    expected_numeric = {
        "qb": 10,  # pass att/cmp/yds/td/int, rush att/yds/td, FL, FPTS
        "rb": 8,   # rush att/yds/td, rec/yds/td, FL, FPTS
        "wr": 8,   # rec/yds/td, rush att/yds/td, FL, FPTS
        "te": 5,   # rec/yds/td, FL, FPTS
    }[position]

    rows = []
    for raw_row in parser.rows:
        if len(raw_row) < expected_numeric + 1:
            continue

        name, team = split_player_team(raw_row[0])
        nums = [to_float(x) for x in raw_row[-expected_numeric:]]

        # Header rows and malformed rows will fail numeric parsing.
        if not name or any(x is None for x in nums):
            continue

        if position == "qb":
            (
                pass_att, pass_cmp, pass_yds, pass_td, pass_int,
                rush_att, rush_yds, rush_td, fum_lost, source_fpts
            ) = nums
            stats = {
                "pass_att": pass_att,
                "pass_cmp": pass_cmp,
                "pass_yd": pass_yds,
                "pass_td": pass_td,
                "pass_int": pass_int,
                "rush_att": rush_att,
                "rush_yd": rush_yds,
                "rush_td": rush_td,
                "fum_lost": fum_lost,
            }
        elif position == "rb":
            (
                rush_att, rush_yds, rush_td,
                rec, rec_yds, rec_td,
                fum_lost, source_fpts
            ) = nums
            stats = {
                "rush_att": rush_att,
                "rush_yd": rush_yds,
                "rush_td": rush_td,
                "rec": rec,
                "rec_yd": rec_yds,
                "rec_td": rec_td,
                "fum_lost": fum_lost,
            }
        elif position == "wr":
            (
                rec, rec_yds, rec_td,
                rush_att, rush_yds, rush_td,
                fum_lost, source_fpts
            ) = nums
            stats = {
                "rec": rec,
                "rec_yd": rec_yds,
                "rec_td": rec_td,
                "rush_att": rush_att,
                "rush_yd": rush_yds,
                "rush_td": rush_td,
                "fum_lost": fum_lost,
            }
        else:
            rec, rec_yds, rec_td, fum_lost, source_fpts = nums
            stats = {
                "rec": rec,
                "rec_yd": rec_yds,
                "rec_td": rec_td,
                "fum_lost": fum_lost,
            }

        rows.append({
            "player_name": name,
            "team": team,
            "position": position.upper(),
            "stats": stats,
            "source_fpts": source_fpts,
        })

    return rows


def score_stats(stats: Dict[str, float], scoring: Dict[str, float]) -> float:
    """
    Score the stat categories actually present in the projection feed.
    Any nonzero FSFFL category that has no source projection is surfaced
    separately in the audit rather than silently invented.
    """
    total = 0.0
    for stat_name, stat_value in stats.items():
        total += float(stat_value or 0.0) * float(scoring.get(stat_name, 0.0))
    return round(total, 3)


def build_prior_index(prior_players: Dict[str, Dict[str, Any]]):
    by_name: Dict[str, List[str]] = {}
    for sid, player in prior_players.items():
        key = norm_name(player.get("player_name") or "")
        if key:
            by_name.setdefault(key, []).append(str(sid))
    return by_name


def choose_sleeper_id(
    row: Dict[str, Any],
    prior_players: Dict[str, Dict[str, Any]],
    by_name: Dict[str, List[str]],
) -> Tuple[Optional[str], str]:
    candidates = by_name.get(norm_name(row["player_name"]), [])
    if not candidates:
        return None, "unmatched_name"

    if len(candidates) == 1:
        return candidates[0], "normalized_name"

    row_team = norm_team(row.get("team"))
    row_pos = row.get("position")

    narrowed = []
    for sid in candidates:
        p = prior_players[sid]
        if (
            norm_team(p.get("team")) == row_team
            and str(p.get("position") or "").upper() == row_pos
        ):
            narrowed.append(sid)

    if len(narrowed) == 1:
        return narrowed[0], "name_team_position"

    return None, "ambiguous_name"


def source_update_label(raw_html: str) -> Optional[str]:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(" ".join(text.split()))
    match = re.search(
        r"Consensus\s+last\s+updated\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
        text,
        flags=re.I,
    )
    return match.group(1) if match else None


def main():
    league = load_json(DATA / "league.json")
    if not league:
        raise RuntimeError("Missing data/league.json")

    season = str(league.get("season") or "").strip()
    if not season:
        raise RuntimeError("data/league.json does not contain an active season")

    scoring = league.get("scoring_settings") or {}
    sim_dir = SIM_ROOT / season
    sources_dir = sim_dir / "sources"
    outputs_dir = sim_dir / "outputs"

    prior_path = sources_dir / "selected_preseason_prior.json"
    prior_payload = load_json(prior_path)
    if not prior_payload or not prior_payload.get("players"):
        raise RuntimeError(
            f"Missing populated preseason prior: {prior_path}. "
            "Run build_fsffl_player_projections.py first."
        )

    prior_players = {
        str(k): v for k, v in prior_payload["players"].items()
    }
    by_name = build_prior_index(prior_players)

    raw_by_position = {}
    parsed_rows = []
    source_dates = {}

    for position in POSITIONS:
        url = FP_URL.format(position=position)
        print(f"Downloading {position.upper()} season projections...")
        raw_html = fetch_text(url)

        # Basic season guard. Refuse obvious wrong-year pages.
        if season not in raw_html:
            raise RuntimeError(
                f"FantasyPros {position.upper()} projection page did not "
                f"contain active season {season}; refusing to use it."
            )

        raw_by_position[position] = raw_html
        source_dates[position] = source_update_label(raw_html)
        parsed_rows.extend(parse_projection_page(position, raw_html))

    if not parsed_rows:
        raise RuntimeError(
            "No projection rows were parsed. FantasyPros page structure may "
            "have changed; no scoring baseline was written."
        )

    # Save latest raw pages only; avoids creating timestamped duplicates on
    # every development run.
    for position, raw_html in raw_by_position.items():
        (sources_dir / f"fantasypros_{position}_season_latest.html").write_text(
            raw_html,
            encoding="utf-8",
        )

    projected = {}
    match_counts: Dict[str, int] = {}
    unmatched = []

    for row in parsed_rows:
        sid, method = choose_sleeper_id(row, prior_players, by_name)
        match_counts[method] = match_counts.get(method, 0) + 1

        if not sid:
            unmatched.append({
                "player_name": row["player_name"],
                "team": row["team"],
                "position": row["position"],
                "reason": method,
            })
            continue

        fsffl_points = score_stats(row["stats"], scoring)

        projected[sid] = {
            "sleeper_id": sid,
            "player_name": prior_players[sid].get("player_name")
                or row["player_name"],
            "position": row["position"],
            "team": prior_players[sid].get("team") or row["team"],
            "season": season,
            "projected_stats": row["stats"],
            "fsffl_projected_points": fsffl_points,
            "source_fpts_reference_only": row["source_fpts"],
            "match_method": method,
            "preseason_ecr": prior_players[sid].get("preseason_ecr"),
            "expert_rank_sd": prior_players[sid].get("expert_rank_sd"),
        }

    rostered_ids = set(prior_players)
    covered_ids = rostered_ids & set(projected)
    missing_rostered = sorted(rostered_ids - covered_ids)

    # These categories are nonzero in FSFFL but are not exposed as projected
    # counting stats on the four FantasyPros position tables.
    projected_stat_names = {
        "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int",
        "rush_att", "rush_yd", "rush_td",
        "rec", "rec_yd", "rec_td", "fum_lost",
    }
    unresolved_nonzero = {
        key: value
        for key, value in scoring.items()
        if float(value or 0.0) != 0.0
        and key not in projected_stat_names
        and key in {"pass_2pt", "rush_2pt", "rec_2pt", "fum_rec", "fum_rec_td"}
    }

    write_json(
        sources_dir / "preseason_fsffl_points.json",
        {
            "season": season,
            "source": "FantasyPros consensus season stat projections",
            "source_urls": {
                p: FP_URL.format(position=p) for p in POSITIONS
            },
            "source_last_updated": source_dates,
            "scoring_source": "data/league.json",
            "note": (
                "FSFFL points are recalculated from raw projected stats using "
                "league scoring. Source FPTS is retained only as a reference "
                "and is not used as the FSFFL total."
            ),
            "players": projected,
        },
    )

    write_json(
        outputs_dir / "preseason_points_audit.json",
        {
            "season": season,
            "parsed_projection_rows": len(parsed_rows),
            "mapped_projection_rows": len(projected),
            "mapping_methods": match_counts,
            "rostered_coverage": {
                "covered": len(covered_ids),
                "total": len(rostered_ids),
                "coverage": round(
                    len(covered_ids) / max(1, len(rostered_ids)), 5
                ),
            },
            "missing_rostered_players": [
                {
                    "sleeper_id": sid,
                    "player_name": prior_players[sid].get("player_name"),
                    "position": prior_players[sid].get("position"),
                    "team": prior_players[sid].get("team"),
                }
                for sid in missing_rostered
            ],
            "unmatched_source_rows": unmatched[:100],
            "source_last_updated": source_dates,
            "unresolved_nonzero_scoring_categories": unresolved_nonzero,
            "quality_gate": {
                "minimum_rostered_coverage": 0.90,
                "passed": (
                    len(covered_ids) / max(1, len(rostered_ids))
                ) >= 0.90,
            },
        },
    )

    coverage = len(covered_ids) / max(1, len(rostered_ids))
    print(
        f"FSFFL preseason scoring baseline complete for {season}: "
        f"{len(covered_ids)}/{len(rostered_ids)} rostered players "
        f"covered ({coverage:.1%})."
    )

    if coverage < 0.90:
        raise RuntimeError(
            "Preseason point projection coverage is below 90%; "
            "quality gate failed."
        )


if __name__ == "__main__":
    main()
