#!/usr/bin/env python3
"""Architecture and first-slice regressions for League Intelligence."""
from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent
path = SCRIPT / "league_intelligence" / "application.py"
spec = importlib.util.spec_from_file_location("league_intelligence_test_application", path)
assert spec is not None and spec.loader is not None
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

payload = app.build_terminal(SCRIPT.parent / "data")
arch = payload["architecture"]
read_only = arch["read_only"]
assert read_only["model_state_mutation"] is False
assert read_only["league_state_mutation"] is False
assert read_only["transaction_execution"] is False
assert read_only["recommendation_authority"] is False
assert read_only["rescoring_authority"] is False

ranking = payload["views"]["player_value_rankings"]
assert ranking["creates_new_player_value"] is False
assert ranking["creates_cross_horizon_composite"] is False
assert ranking["recommendation"] is False
assert ranking["player_count"] > 0
rows = ranking["players"]
market_ranks = [r["long_term_market_rank"] for r in rows]
assert market_ranks == list(range(1, len(rows) + 1))
market_values = [r["long_term_market_value"] for r in rows]
assert market_values == sorted(market_values, reverse=True)
assert any(r["current_season_projection"]["available"] for r in rows)
projection_ranks = sorted(
    r["current_season_projection_rank"]
    for r in rows if r["current_season_projection_rank"] is not None
)
assert projection_ranks == list(range(1, len(projection_ranks) + 1))
assert all("raw_native_model_value" not in r for r in rows)
assert all(
    r["published_fsffl_value_status"]["available_for_ranking"] is False
    for r in rows
)
assert all(r["field_provenance"]["ranks"]["creates_new_value"] is False for r in rows)

value_contract = payload["contract_health"]["player_value_authority"]
assert value_contract["quarantined_player_count"] > 0
assert value_contract["market_anchor_alias_count"] > 0
assert value_contract["terminal_uses_published_fsffl_value_for_ranking"] is False
assert value_contract["quarantine_enforced"] is True
assert value_contract["active_rankings_safe_for_presentation"] is True
assert value_contract["authoritative_model_vs_market_available"] is False
assert payload["capability_status"]["model_vs_market"] is False
assert payload["capability_status"]["model_vs_market_blocked_by_source_contract"] is True

landscape = payload["views"]["league_competitive_landscape"]
assert landscape["creates_independent_power_score"] is False
assert len(landscape["teams"]) == 12

contract = payload["contract_health"]["competitive_state_strategic_posture"]
assert contract["terminal_consumes_incompatible_artifacts"] is False
if contract["incompatible_artifacts"]:
    assert contract["compatible"] is False

markdown = app.render_player_rankings_markdown(payload, limit=10)
assert "FSFFL Player Value & Rankings Terminal" in markdown
assert "Long-term market ranking" in markdown
assert "Current-season projection ranking" in markdown
assert "Model-versus-market ranking: unavailable" in markdown
assert "pre-separation team-profile artifacts are excluded" in markdown

print("League Intelligence Terminal first-slice regressions passed")
