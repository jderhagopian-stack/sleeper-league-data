#!/usr/bin/env python3
"""Fetch and normalize FFToday season projections for the FSFFL ensemble.

FFToday publishes public season-long statistical projections for QB/RB/WR/TE.
This adapter parses the raw projected stats, maps players to the existing Sleeper
preseason universe, and recalculates fantasy points under data/league.json. The
provider's own fantasy-point column is intentionally ignored.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA = Path("data")
SIM_ROOT = DATA / "simulator"
BASE_URL = "https://www.fftoday.com/rankings/playerproj.php"
POSITION_IDS = {"QB": 10, "RB": 20, "WR": 30, "TE": 40}
MAX_PAGES = 6
TEAM_ALIASES = {"JAC": "JAX", "NEP": "NE", "KCC": "KC", "SFO": "SF", "GBP": "GB", "NOS": "NO", "TBB": "TB", "LVR": "LV"}
NFL_TEAMS = {
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
    "HOU","IND","JAX","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG",
    "NYJ","PHI","PIT","SEA","SF","TB","TEN","WAS"
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def norm_team(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).upper().strip()
    return TEAM_ALIASES.get(value, value)


def finite_float(value: str) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class RowParser(HTMLParser):
    """Collect visible text by HTML table row/cell without third-party packages."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._in_row = False
        self._in_cell = False
        self._cells: List[str] = []
        self._parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._in_row = True
            self._cells = []
        elif self._in_row and tag in ("td", "th"):
            self._in_cell = True
            self._parts = []

    def handle_data(self, data):
        if self._in_cell:
            text = data.strip()
            if text:
                self._parts.append(text)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._in_row and self._in_cell and tag in ("td", "th"):
            self._cells.append(re.sub(r"\s+", " ", " ".join(self._parts)).strip())
            self._in_cell = False
            self._parts = []
        elif self._in_row and tag == "tr":
            if self._cells:
                self.rows.append(self._cells)
            self._in_row = False
            self._in_cell = False


def fetch_page(season: str, position: str, page: int) -> Tuple[str, str]:
    query = urllib.parse.urlencode({
        "LeagueID": "1",
        "PosID": POSITION_IDS[position],
        "Season": season,
        "cur_page": page,
    })
    url = f"{BASE_URL}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "FSFFL-Projection-Ensemble/1.0 (+personal fantasy-football research)",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        html = response.read().decode("utf-8", errors="replace")
    return url, html


def prior_name_index(prior_players: Dict[str, Dict[str, Any]], position: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for sid, player in prior_players.items():
        if str(player.get("position") or "").upper() != position:
            continue
        key = norm_name(player.get("player_name") or "")
        if key:
            out.setdefault(key, []).append(str(sid))
    return out


def identify_player_cell(cells: List[str], by_name: Dict[str, List[str]]) -> Tuple[Optional[int], Optional[str]]:
    # Exact normalized-name match first. Some FFToday cells include a short risk/
    # upside annotation, so a longest-prefix match is the conservative fallback.
    for idx, cell in enumerate(cells):
        key = norm_name(cell)
        if key in by_name:
            return idx, key
    best: Tuple[int, str] | None = None
    for idx, cell in enumerate(cells):
        value = norm_name(cell)
        for key in by_name:
            if len(key) >= 5 and value.startswith(key):
                if best is None or len(key) > len(best[1]):
                    best = (idx, key)
    return best if best else (None, None)


def choose_sid(
    key: str,
    team: str,
    by_name: Dict[str, List[str]],
    prior_players: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[str], str]:
    candidates = by_name.get(key, [])
    if len(candidates) == 1:
        return candidates[0], "normalized_name"
    narrowed = [sid for sid in candidates if norm_team(prior_players[sid].get("team")) == team]
    if len(narrowed) == 1:
        return narrowed[0], "name_team"
    return None, "ambiguous_name"


def stats_from_numbers(position: str, numbers: List[float]) -> Optional[Dict[str, float]]:
    # numbers begins with bye week and ends with FFToday FPts (which is ignored).
    if position == "QB" and len(numbers) >= 10:
        _, cmp_, att, pyd, ptd, pint, ratt, ryd, rtd, _ = numbers[:10]
        return {"pass_cmp":cmp_,"pass_att":att,"pass_yd":pyd,"pass_td":ptd,"pass_int":pint,"rush_att":ratt,"rush_yd":ryd,"rush_td":rtd,"fum_lost":0.0}
    if position == "RB" and len(numbers) >= 8:
        _, ratt, ryd, rtd, rec, reyd, retd, _ = numbers[:8]
        return {"rush_att":ratt,"rush_yd":ryd,"rush_td":rtd,"rec":rec,"rec_yd":reyd,"rec_td":retd,"fum_lost":0.0}
    if position == "WR" and len(numbers) >= 8:
        _, rec, reyd, retd, ratt, ryd, rtd, _ = numbers[:8]
        return {"rec":rec,"rec_yd":reyd,"rec_td":retd,"rush_att":ratt,"rush_yd":ryd,"rush_td":rtd,"fum_lost":0.0}
    if position == "TE" and len(numbers) >= 5:
        _, rec, reyd, retd, _ = numbers[:5]
        return {"rec":rec,"rec_yd":reyd,"rec_td":retd,"fum_lost":0.0}
    return None


def score_stats(stats: Dict[str, float], scoring: Dict[str, float]) -> float:
    return round(sum(float(v or 0.0) * float(scoring.get(k, 0.0)) for k, v in stats.items()), 3)


def parse_position(
    season: str,
    position: str,
    prior_players: Dict[str, Dict[str, Any]],
    scoring: Dict[str, float],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    by_name = prior_name_index(prior_players, position)
    players: Dict[str, Dict[str, Any]] = {}
    unmatched: List[Dict[str, Any]] = []
    pages: List[Dict[str, Any]] = []
    combined_hash = hashlib.sha256()
    updated_dates = set()
    prior_keys = set(by_name)

    for page in range(MAX_PAGES):
        url, html = fetch_page(season, position, page)
        combined_hash.update(html.encode("utf-8"))
        updated = re.search(r"Updated:\s*</?[^>]*>?\s*(\d{1,2}/\d{1,2}/\d{4})", html, re.I)
        if not updated:
            updated = re.search(r"Updated:\s*(\d{1,2}/\d{1,2}/\d{4})", re.sub(r"<[^>]+>", " ", html), re.I)
        if updated:
            updated_dates.add(updated.group(1))

        parser = RowParser()
        parser.feed(html)
        before = len(players)
        matched_rows = 0
        for cells in parser.rows:
            player_idx, key = identify_player_cell(cells, by_name)
            if player_idx is None or not key:
                continue

            team_idx = None
            team = None
            for idx in range(player_idx + 1, min(len(cells), player_idx + 5)):
                candidate = norm_team(cells[idx])
                if candidate in NFL_TEAMS:
                    team_idx, team = idx, candidate
                    break
            if team_idx is None or not team:
                continue

            numeric = []
            for cell in cells[team_idx + 1:]:
                value = finite_float(cell)
                if value is not None:
                    numeric.append(value)
            stats = stats_from_numbers(position, numeric)
            if not stats:
                continue

            sid, method = choose_sid(key, team, by_name, prior_players)
            if not sid:
                unmatched.append({"player_cell":cells[player_idx],"team":team,"position":position,"reason":method})
                continue
            points = score_stats(stats, scoring)
            prior = prior_players[sid]
            players[sid] = {
                "sleeper_id": sid,
                "player_name": prior.get("player_name") or cells[player_idx],
                "team": prior.get("team") or team,
                "position": position,
                "season": season,
                "games_projected": 17.0,
                "games_projection_source": "not_published_by_fftoday_adapter; season_total_spread_over_17_regular_season_games",
                "projected_stats": stats,
                "fsffl_projected_points": points,
                "fsffl_projected_ppg": round(points / 17.0, 3),
                "match_method": method,
                "source": "FFToday",
            }
            matched_rows += 1
        pages.append({"page":page,"url":url,"html_rows":len(parser.rows),"matched_rows":matched_rows,"new_players":len(players)-before})

        # Once a later page contributes no new known players, further pages are
        # unlikely to improve fantasy-relevant coverage. The hard cap prevents a
        # bad pagination response from looping indefinitely.
        if page >= 1 and len(players) == before:
            break
        if prior_keys and len(players) >= sum(len(v) for v in by_name.values()) * 0.98:
            break

    return players, {
        "pages": pages,
        "source_content_sha256": combined_hash.hexdigest(),
        "source_updated_dates": sorted(updated_dates),
        "unmatched_rows": unmatched[:150],
    }


def main():
    league = load_json(DATA / "league.json")
    if not league:
        raise RuntimeError("Missing data/league.json")
    season = str(league.get("season") or "").strip()
    scoring = league.get("scoring_settings") or {}
    if not season:
        raise RuntimeError("Active season missing from data/league.json")

    sources_dir = SIM_ROOT / season / "sources"
    prior = load_json(sources_dir / "selected_preseason_prior.json")
    if not prior or not prior.get("players"):
        raise RuntimeError("Missing populated selected_preseason_prior.json for Sleeper player matching")
    prior_players = {str(k): v for k, v in prior["players"].items()}

    players: Dict[str, Dict[str, Any]] = {}
    audit_by_position = {}
    hashes = hashlib.sha256()
    for position in POSITION_IDS:
        position_players, audit = parse_position(season, position, prior_players, scoring)
        players.update(position_players)
        audit_by_position[position] = audit
        hashes.update(audit["source_content_sha256"].encode("ascii"))

    rostered_ids = {
        sid for sid, p in prior_players.items()
        if str(p.get("position") or "").upper() in POSITION_IDS
    }
    covered_ids = rostered_ids & set(players)
    coverage = len(covered_ids) / max(1, len(rostered_ids))
    retrieved_at = datetime.now(timezone.utc).isoformat()

    out = {
        "season": season,
        "source_id": "fftoday",
        "source_name": "FFToday",
        "provenance_class": "EXTERNAL_CURRENT_FORECAST",
        "projection_horizon": "preseason_regular_season",
        "scoring_source": "data/league.json",
        "retrieved_at_utc": retrieved_at,
        "normalized_snapshot_sha256": hashes.hexdigest(),
        "source_method": "public FFToday season projection tables; raw stats rescored under FSFFL rules",
        "players": players,
        "audit": {
            "mapped_players": len(players),
            "eligible_prior_players": len(rostered_ids),
            "rostered_coverage": round(coverage, 5),
            "positions": audit_by_position,
        },
    }
    write_json(sources_dir / "projection_fftoday.json", out)
    print(f"FFToday source normalized: {len(covered_ids)}/{len(rostered_ids)} eligible players ({coverage:.1%}).")
    if coverage < 0.85:
        raise RuntimeError("FFToday projection coverage below 85%; source adapter quality gate failed")


if __name__ == "__main__":
    main()
