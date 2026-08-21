#!/usr/bin/env python3
"""
FSFFL scoring baseline builder - Razzball full-season projection adapter.

Primary source:
- Razzball 2026 rest-of-season projection tables for QB/RB/WR/TE.

Secondary QC:
- Existing selected_preseason_prior.json (FantasyPros superflex ECR prior).

Output:
- data/simulator/<season>/sources/preseason_fsffl_points.json
- data/simulator/<season>/outputs/preseason_points_audit.json

This script recalculates points using data/league.json scoring rules.
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

RAZZBALL_URLS = {
    "QB": "https://football.razzball.com/projections-qb-restofseason/",
    "RB": "https://football.razzball.com/projections-rb-restofseason/",
    "WR": "https://football.razzball.com/projections-wr-restofseason/",
    "TE": "https://football.razzball.com/projections-te-restofseason/",
}

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
    "LAR": "LAR",
    "LAC": "LAC",
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
    if not text or text.upper() in {"NA", "N/A", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def norm_team(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).upper().strip()
    return TEAM_ALIASES.get(value, value)


def clean_header(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("%", " pct ")
    value = value.replace("1/2", " half ")
    value = value.replace("/", " ")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


class AllTablesParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_cell: List[str] = []
        self.current_row: List[str] = []
        self.current_table: List[List[str]] = []
        self.tables: List[List[List[str]]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if not self.in_table:
                self.in_table = True
                self.current_table = []
            return

        if not self.in_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in {"th", "td"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.in_cell:
            text = " ".join("".join(self.current_cell).split())
            self.current_row.append(text)
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.current_table.append(self.current_row)
            self.current_row = []
            self.in_row = False
        elif tag == "table" and self.in_table:
            if self.current_table:
                self.tables.append(self.current_table)
            self.current_table = []
            self.in_table = False

    def handle_data(self, data):
        if self.in_table and self.in_cell:
            self.current_cell.append(data)


def find_projection_table(raw_html: str) -> List[List[str]]:
    parser = AllTablesParser()
    parser.feed(raw_html)

    best = None
    best_score = -1

    for table in parser.tables:
        if not table:
            continue

        # Find a row that looks like the projection header.
        for idx, row in enumerate(table[:6]):
            headers = [clean_header(x) for x in row]
            score = 0
            if "name" in headers:
                score += 3
            if "team" in headers:
                score += 3
            if any("pts" in h for h in headers):
                score += 2
            if any(h in {"rush", "rec", "att", "cmp", "pass_yds", "rec_yds"} for h in headers):
                score += 2

            # Prefer the largest player table, not the "ruled out" mini-table.
            score += min(len(table), 500) / 1000.0

            if score > best_score:
                best_score = score
                best = table[idx:]

    if not best or best_score < 6:
        raise RuntimeError("Could not locate full Razzball projection table.")

    return best


def row_dicts_from_table(table: List[List[str]]) -> List[Dict[str, str]]:
    header = [clean_header(x) for x in table[0]]
    rows = []

    for raw in table[1:]:
        if len(raw) != len(header):
            continue
        row = dict(zip(header, raw))
        if not row.get("name") or not row.get("team"):
            continue
        rows.append(row)

    return rows


def get_number(row: Dict[str, str], *keys: str, default=0.0) -> float:
    for key in keys:
        if key in row:
            value = to_float(row.get(key))
            if value is not None:
                return value
    return float(default)


def projected_stats(position: str, row: Dict[str, str]) -> Dict[str, float]:
    stats: Dict[str, float] = {}

    if position == "QB":
        stats.update({
            "pass_att": get_number(row, "att"),
            "pass_cmp": get_number(row, "cmp"),
            "pass_yd": get_number(row, "pass_yds"),
            "pass_td": get_number(row, "pass_td"),
            "pass_int": get_number(row, "int"),
            "rush_att": get_number(row, "rush"),
            "rush_yd": get_number(row, "rush_yds"),
            "rush_td": get_number(row, "run_td"),
            "fum_lost": get_number(row, "fum_lst", "fum_lost"),
        })

    elif position == "RB":
        stats.update({
            "rush_att": get_number(row, "rush"),
            "rush_yd": get_number(row, "rush_yds"),
            "rush_td": get_number(row, "run_td"),
            "rec": get_number(row, "rec"),
            "rec_yd": get_number(row, "rec_yds"),
            "rec_td": get_number(row, "rec_td"),
            "fum_lost": get_number(row, "fum_lst", "fum_lost"),
        })

    elif position == "WR":
        stats.update({
            "rec": get_number(row, "rec"),
            "rec_yd": get_number(row, "rec_yds"),
            "rec_td": get_number(row, "rec_td"),
            "rush_att": get_number(row, "rush"),
            "rush_yd": get_number(row, "rush_yds"),
            "rush_td": get_number(row, "run_td"),
            "fum_lost": get_number(row, "fum_lst", "fum_lost"),
        })

    elif position == "TE":
        stats.update({
            "rec": get_number(row, "rec"),
            "rec_yd": get_number(row, "rec_yds"),
            "rec_td": get_number(row, "rec_td"),
            "rush_att": get_number(row, "rush"),
            "rush_yd": get_number(row, "rush_yds"),
            "rush_td": get_number(row, "run_td"),
            "fum_lost": get_number(row, "fum_lst", "fum_lost"),
        })

    return stats


def score_stats(stats: Dict[str, float], scoring: Dict[str, float]) -> float:
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
    name: str,
    team: Optional[str],
    position: str,
    prior_players: Dict[str, Dict[str, Any]],
    by_name: Dict[str, List[str]],
) -> Tuple[Optional[str], str]:
    candidates = by_name.get(norm_name(name), [])
    if not candidates:
        return None, "unmatched_name"

    if len(candidates) == 1:
        return candidates[0], "normalized_name"

    team = norm_team(team)
    narrowed = [
        sid for sid in candidates
        if norm_team(prior_players[sid].get("team")) == team
        and str(prior_players[sid].get("position") or "").upper() == position
    ]

    if len(narrowed) == 1:
        return narrowed[0], "name_team_position"

    return None, "ambiguous_name"


def extract_updated_label(raw_html: str) -> Optional[str]:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(" ".join(text.split()))
    match = re.search(
        r"Updated:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+[AP]M\s+EST)",
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
        raise RuntimeError("Active season missing from data/league.json")

    scoring = league.get("scoring_settings") or {}

    sim_dir = SIM_ROOT / season
    sources_dir = sim_dir / "sources"
    outputs_dir = sim_dir / "outputs"
    sources_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    prior_path = sources_dir / "selected_preseason_prior.json"
    prior_payload = load_json(prior_path)
    if not prior_payload or not prior_payload.get("players"):
        raise RuntimeError(
            f"Missing populated preseason prior: {prior_path}"
        )

    prior_players = {
        str(k): v for k, v in prior_payload["players"].items()
    }
    by_name = build_prior_index(prior_players)

    projected: Dict[str, Dict[str, Any]] = {}
    source_dates = {}
    parsed_counts = {}
    match_counts: Dict[str, int] = {}
    unmatched = []

    for position, url in RAZZBALL_URLS.items():
        print(f"Downloading {position} Razzball season projections...")
        raw_html = fetch_text(url)

        if season not in raw_html:
            raise RuntimeError(
                f"Razzball {position} page does not contain active season "
                f"{season}; refusing to use it."
            )

        source_dates[position] = extract_updated_label(raw_html)

        raw_path = sources_dir / f"razzball_{position.lower()}_season_latest.html"
        raw_path.write_text(raw_html, encoding="utf-8")

        table = find_projection_table(raw_html)
        rows = row_dicts_from_table(table)
        parsed_counts[position] = len(rows)

        for row in rows:
            name = row.get("name", "").strip()
            team = norm_team(row.get("team"))

            sid, method = choose_sleeper_id(
                name,
                team,
                position,
                prior_players,
                by_name,
            )
            match_counts[method] = match_counts.get(method, 0) + 1

            if not sid:
                unmatched.append({
                    "player_name": name,
                    "team": team,
                    "position": position,
                    "reason": method,
                })
                continue

            stats = projected_stats(position, row)
            points = score_stats(stats, scoring)

            projected[sid] = {
                "sleeper_id": sid,
                "player_name": prior_players[sid].get("player_name") or name,
                "team": prior_players[sid].get("team") or team,
                "position": position,
                "season": season,
                "games_projected": get_number(row, "g", "games"),
                "projected_stats": stats,
                "fsffl_projected_points": points,
                "fsffl_projected_ppg": round(
                    points / max(1.0, get_number(row, "g", "games", default=17.0)),
                    3,
                ),
                "razzball_half_ppr_points_reference": get_number(
                    row, "half_ppr_pts", default=0.0
                ),
                "razzball_half_ppr_ppg_reference": get_number(
                    row, "half_ppr_ppg", default=0.0
                ),
                "preseason_ecr": prior_players[sid].get("preseason_ecr"),
                "expert_rank_sd": prior_players[sid].get("expert_rank_sd"),
                "match_method": method,
                "source": "Razzball",
            }

    rostered_ids = set(prior_players)
    covered_ids = rostered_ids & set(projected)
    missing_ids = sorted(rostered_ids - covered_ids)

    coverage = len(covered_ids) / max(1, len(rostered_ids))

    write_json(
        sources_dir / "preseason_fsffl_points.json",
        {
            "season": season,
            "source": "Razzball full-season projections",
            "source_urls": RAZZBALL_URLS,
            "source_last_updated": source_dates,
            "scoring_source": "data/league.json",
            "players": projected,
        },
    )

    write_json(
        outputs_dir / "preseason_points_audit.json",
        {
            "season": season,
            "source": "Razzball",
            "parsed_rows_by_position": parsed_counts,
            "mapped_players": len(projected),
            "mapping_methods": match_counts,
            "rostered_coverage": {
                "covered": len(covered_ids),
                "total": len(rostered_ids),
                "coverage": round(coverage, 5),
            },
            "missing_rostered_players": [
                {
                    "sleeper_id": sid,
                    "player_name": prior_players[sid].get("player_name"),
                    "position": prior_players[sid].get("position"),
                    "team": prior_players[sid].get("team"),
                }
                for sid in missing_ids
            ],
            "unmatched_source_rows": unmatched[:150],
            "source_last_updated": source_dates,
            "quality_gate": {
                "minimum_rostered_coverage": 0.90,
                "passed": coverage >= 0.90,
            },
        },
    )

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
