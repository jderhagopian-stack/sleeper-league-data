#!/usr/bin/env python3
"""Regression and readability checks for the League Intelligence PDF."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "script"
FOCUS = "846634401482792960"


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    terminal_path = tmp_path / "terminal.json"
    pdf_path = tmp_path / "league-intelligence.pdf"
    subprocess.run([
        sys.executable,
        str(SCRIPT / "league_intelligence" / "application.py"),
        "--focus-user-id", FOCUS,
        "--team-context", str(ROOT / "data" / "league_intelligence" / "hurts_so_good_team_context.json"),
        "--output", str(terminal_path),
    ], cwd=ROOT, check=True, capture_output=True, text=True)
    source = json.loads(terminal_path.read_text(encoding="utf-8"))
    subprocess.run([
        sys.executable,
        str(SCRIPT / "render_league_intelligence_report.py"),
        "--input", str(terminal_path),
        "--output", str(pdf_path),
        "--focus-user-id", FOCUS,
    ], cwd=ROOT, check=True, capture_output=True, text=True)

    assert pdf_path.exists() and pdf_path.stat().st_size > 20_000
    assert pdf_path.read_bytes()[:4] == b"%PDF"
    reader = PdfReader(str(pdf_path))
    assert len(reader.pages) == 8, len(reader.pages)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    lower = text.lower()
    for phrase in (
        "fsffl league intelligence",
        "the competitive landscape",
        "your roster shape",
        "league strength / weakness heat map",
        "player value & rankings",
        "value to your team",
        "roster dependence & trade-partner intelligence",
        "decision inspector & source health",
        "not player prices",
        "53 quarantined",
    ):
        assert phrase in lower, phrase
    for banned in (
        "acceptance probability: 50",
        "recommended trade",
        "state-aware",
        "break-glass",
        "heuristic acceptance",
        "post-sim",
        "adaptive confirmation",
        "simulation multiverse",
    ):
        assert banned not in lower, banned
    assert "do not establish a fair price" in lower
    assert "..." not in text and "\u2026" not in text
    assert source["architecture"]["read_only"]["recommendation_authority"] is False
    assert source["views"]["trade_partner_intelligence"]["recommendation"] is False
    assert source["views"]["trade_partner_intelligence"]["acceptance_probability"] is False

print("League Intelligence polished PDF regressions passed")
