#!/usr/bin/env python3
"""Build a current 2026 FFToday raw-stat reference for the Projection Championship.

This adapter is deliberately non-authoritative. It normalizes FFToday's public
preseason projection pages to Sleeper IDs and FSFFL raw-stat/scoring contracts so
accuracy research can compare current sources on identical player/stat rows.

Usage rights are a separate gate. This script does not promote FFToday into the
Simulator authority and the emitted artifact is marked RESEARCH_ONLY until the
source registry explicitly permits the requested deployment context.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
sys.path.insert(0, str(SCRIPT))

from build_fsffl_scoring_baseline import (  # noqa: E402
    build_prior_index,
    choose_sleeper_id,
    load_json,
    score_stats,
)
from run_native_vs_fftoday_historical_benchmark import (  # noqa: E402
    LAYOUT,
    POS_ID,
    TEAM_CODES,
    TableParser,
    fetch_html,
    norm_name,
    num,
    player_from_cell,
)

DATA = ROOT / "data"
CANONICAL = {
    "completions": "pass_cmp",
    "attempts": "pass_att",
    "passing_yards": "pass_yd",
    "passing_tds": "pass_td",
    "interceptions": "pass_int",
    "carries": "rush_att",
    "rushing_attempts": "rush_att",
    "rushing_yards": "rush_yd",
    "rushing_tds": "rush_td",
    "receptions": "rec",
    "receiving_yards": "rec_yd",
    "receiving_tds": "rec_td",
}
POSITIONS = ("QB", "RB", "WR", "TE")


def parse_page(html: str, position: str) -> list[dict]:
    parser = TableParser()
    parser.feed(html)
    rows = []
    for row in parser.rows:
        cells = [c.get("text", "").strip() for c in row]
        team_i = next((i for i, value in enumerate(cells) if value in TEAM_CODES), None)
        if team_i is None or team_i < 1 or team_i + 2 >= len(cells):
            continue
        name = player_from_cell(row[team_i - 1])
        if not name or not any(ch.isalpha() for ch in name):
            continue
        tail = cells[team_i + 1:]  # bye, then projection columns
        try:
            parsed = {"player_name": name, "team": cells[team_i]}
            for stat, idx in LAYOUT[position]:
                parsed[stat] = num(tail[idx])
        except (IndexError, ValueError):
            continue
        rows.append(parsed)
    dedup = {}
    for row in rows:
        dedup[(norm_name(row["player_name"]), row["team"])] = row
    return list(dedup.values())


def fetch_current(season: int, position: str) -> tuple[list[dict], str]:
    all_rows, seen = [], set()
    updated = None
    for page in range(0, 10):
        query = urllib.parse.urlencode({
            "LeagueID": 1,
            "PosID": POS_ID[position],
            "Season": season,
            "cur_page": page,
            "order_by": "FName",
            "sort_order": "ASC",
        })
        html = fetch_html("https://www.fftoday.com/rankings/playerproj.php?" + query)
        if page == 0:
            text = re.sub(r"<[^>]+>", " ", html)
            match = re.search(r"Updated:\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
            if not match:
                raise RuntimeError(f"{position}: FFToday update date not found")
            updated = datetime.strptime(match.group(1), "%m/%d/%Y").date().isoformat()
            if str(season) not in text:
                raise RuntimeError(f"{position}: page does not identify requested season {season}")
        parsed = parse_page(html, position)
        new = 0
        for row in parsed:
            key = (norm_name(row["player_name"]), row["team"])
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
                new += 1
        if page > 0 and new == 0:
            break
    if not all_rows or not updated:
        raise RuntimeError(f"{position}: no current FFToday rows parsed")
    return all_rows, updated


def canonical_stats(row: dict) -> dict:
    out = {}
    for raw, canonical in CANONICAL.items():
        if raw in row:
            out[canonical] = round(float(row[raw]), 3)
    return out


def build(season: int = 2026) -> dict:
    league = load_json(DATA / "league.json") or {}
    scoring = league.get("scoring_settings") or {}
    sources = DATA / "simulator" / str(season) / "sources"
    prior = load_json(sources / "selected_preseason_prior.json") or {}
    prior_players = {str(k): v for k, v in (prior.get("players") or {}).items()}
    if not prior_players:
        raise RuntimeError("selected_preseason_prior.json is missing populated player mappings")
    by_name = build_prior_index(prior_players)

    players = {}
    updated_by_position = {}
    parsed_by_position = {}
    mapping_methods = {}
    unmatched = []

    for position in POSITIONS:
        rows, updated = fetch_current(season, position)
        updated_by_position[position] = updated
        parsed_by_position[position] = len(rows)
        for source_row in rows:
            sid, method = choose_sleeper_id(
                source_row["player_name"],
                source_row.get("team"),
                position,
                prior_players,
                by_name,
            )
            mapping_methods[method] = mapping_methods.get(method, 0) + 1
            if not sid:
                unmatched.append({
                    "player_name": source_row["player_name"],
                    "team": source_row.get("team"),
                    "position": position,
                    "reason": method,
                })
                continue
            stats = canonical_stats(source_row)
            points = score_stats(stats, scoring)
            players[sid] = {
                "sleeper_id": sid,
                "player_name": prior_players[sid].get("player_name") or source_row["player_name"],
                "team": prior_players[sid].get("team") or source_row.get("team"),
                "position": position,
                "season": str(season),
                "projected_stats": stats,
                "raw_stats_fftoday": {
                    k: v for k, v in source_row.items()
                    if k not in {"player_name", "team"}
                },
                "fsffl_projected_points": points,
                "source": "FFToday",
                "source_role": "PROJECTION_CHAMPIONSHIP_RESEARCH_REFERENCE_ONLY",
            }

    rostered = set(prior_players)
    covered = rostered & set(players)
    coverage = len(covered) / max(1, len(rostered))
    payload = {
        "season": str(season),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "FFToday current preseason raw-stat projections",
        "source_url_template": "https://www.fftoday.com/rankings/playerproj.php?LeagueID=1&PosID={PosID}&Season={season}",
        "source_last_updated": updated_by_position,
        "authority": {
            "role": "PROJECTION_CHAMPIONSHIP_RESEARCH_REFERENCE_ONLY",
            "simulator_authority": False,
            "personal_research_dependency_eligible": False,
            "reason": "Current public access is verified; unrestricted personal model-ingestion/reuse permission remains unresolved in the governed source registry.",
        },
        "players": players,
        "audit": {
            "parsed_rows_by_position": parsed_by_position,
            "mapped_players": len(players),
            "mapping_methods": mapping_methods,
            "rostered_coverage": {
                "covered": len(covered),
                "total": len(rostered),
                "coverage": round(coverage, 5),
            },
            "unmatched_source_rows": unmatched[:150],
        },
    }
    out = sources / "fftoday_preseason_fsffl_points_reference.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "source": payload["source"],
        "mapped_players": len(players),
        "rostered_coverage": round(coverage, 5),
        "output": str(out),
    }, indent=2))
    return payload


def self_test() -> None:
    stats = canonical_stats({
        "attempts": 500,
        "completions": 330,
        "passing_yards": 4000,
        "passing_tds": 30,
        "interceptions": 10,
        "rushing_yards": 250,
        "rushing_tds": 3,
    })
    assert stats["pass_att"] == 500
    assert stats["pass_yd"] == 4000
    assert stats["rush_td"] == 3
    assert "attempts" not in stats
    print("FFToday 2026 projection reference self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    build(args.season)


if __name__ == "__main__":
    main()
