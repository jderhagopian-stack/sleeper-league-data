#!/usr/bin/env python3
"""Regression checks for the reproducible preseason media guide report."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

import render_preseason_media_guide as report


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    rosters = report.load(ROOT / "data/rosters.json")
    users = report.load(ROOT / "data/users.json")
    players = report.load(ROOT / "data/players.json")
    projections = report.load(ROOT / "data/simulator/2026/sources/preseason_fsffl_points.json")
    simulator = report.load(ROOT / "data/gm/league/simulator_context.json")

    teams = report.build_rosters(rosters, users, players, projections)
    assert len(teams) == 12
    assert sum(len(team["players"]) for team in teams) == 243
    assert len(simulator.get("teams") or []) == 12

    # The report may organize the roster, but it must never create projection rows.
    source_projection_ids = set((projections.get("players") or {}).keys())
    report_projection_ids = {
        row["player_id"]
        for team in teams
        for row in team["players"]
        if row.get("projected_points") is not None
    }
    assert report_projection_ids <= source_projection_ids

    for team in teams:
        lineup = report.projection_optimized_lineup(team)
        assert len(lineup) == 9, (team["team_name"], len(lineup))
        lineup_ids = [row["player_id"] for _, row in lineup]
        assert len(lineup_ids) == len(set(lineup_ids))
        assert not [
            row for _, row in lineup
            if row.get("roster_status") in {"TAXI", "RESERVE"}
        ]
        slots = [slot for slot, _ in lineup]
        assert slots.count("QB") == 1
        assert slots.count("RB") == 2
        assert slots.count("WR") == 3
        assert slots.count("TE") == 1
        assert slots.count("FLEX") == 1
        assert slots.count("SUPERFLEX") == 1

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        li = td / "league-intelligence.json"
        pdf = td / "preseason-media-guide.pdf"
        subprocess.run([
            "python",
            str(ROOT / "script/league_intelligence/application.py"),
            "--focus-user-id",
            "846634401482792960",
            "--team-context",
            str(ROOT / "data/league_intelligence/hurts_so_good_team_context.json"),
            "--output",
            str(li),
        ], cwd=ROOT, check=True)
        report.render(
            pdf,
            rosters_path=ROOT / "data/rosters.json",
            users_path=ROOT / "data/users.json",
            players_path=ROOT / "data/players.json",
            projections_path=ROOT / "data/simulator/2026/sources/preseason_fsffl_points.json",
            simulator_path=ROOT / "data/gm/league/simulator_context.json",
            league_intelligence_path=li,
        )
        assert pdf.exists() and pdf.stat().st_size > 15000
        reader = PdfReader(str(pdf))
        assert 28 <= len(reader.pages) <= 32, len(reader.pages)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        for team in teams:
            assert team["team_name"] in text
        assert "Full 2026 Projected Roster" in text
        assert "projection-optimized" in text.lower()
        assert "No supported projection" in text
        assert "presentation-only" in text.lower()

    architecture = json.loads(
        (ROOT / "data/reporting/fsffl_reporting_architecture.json").read_text(encoding="utf-8")
    )
    supported = {row["id"]: row for row in architecture["supported_reports"]}
    assert supported["preseason_media_guide"]["status"] == "implemented"
    contract = architecture["preseason_media_guide_contract"]
    assert contract["decision_authority"] is False
    assert contract["rescoring_authority"] is False
    assert contract["projection_authority"] is False
    assert contract["projected_lineup_display_transform"]["creates_new_model_score"] is False
    assert contract["projected_lineup_display_transform"]["creates_recommendation"] is False

    print("Preseason media guide report regression checks passed")


if __name__ == "__main__":
    main()
