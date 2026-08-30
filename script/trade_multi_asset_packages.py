#!/usr/bin/env python3
"""Canonical multi-asset trade package generator.

Mechanical extraction of the production v1.16 candidate-package expansion.

Search universe:
- up to 2 players;
- up to 4 picks when players are included;
- up to 5 total incoming assets when players are included;
- pick-only packages up to 5 picks.

This module only enumerates candidate return packages. Downstream pruning,
simulation, buyer plausibility, bilateral gates, and final decision authority
remain separate.
"""
from __future__ import annotations

import itertools

MODEL_VERSION = "FSFFL-Multi-Asset-Package-Generator-1.0"
MAX_PLAYERS = 2
MAX_PICKS = 4
MAX_TOTAL_ASSETS = 5
MAX_PICK_ONLY = 5


def candidate_packages(assets, max_players=MAX_PLAYERS, max_picks=MAX_PICKS):
    players = [a for a in assets if a.get("asset_type") == "player"]
    picks = [a for a in assets if a.get("asset_type") == "pick"]
    seen = set()
    pmax = min(MAX_PLAYERS, max_players, len(players))

    for np in range(0, pmax + 1):
        nk_cap = min(MAX_PICK_ONLY if np == 0 else MAX_PICKS, len(picks))
        for nk in range(0, nk_cap + 1):
            total = np + nk
            if total == 0:
                continue
            if np > 0 and total > MAX_TOTAL_ASSETS:
                continue
            for pc in itertools.combinations(players, np):
                for kc in itertools.combinations(picks, nk):
                    pkg = list(pc + kc)
                    key = tuple(sorted(str(a.get("asset_id")) for a in pkg))
                    if key in seen:
                        continue
                    seen.add(key)
                    yield pkg


def policy():
    return {
        "multi_asset_package_generator_model_version": MODEL_VERSION,
        "league_realistic_multi_asset_search": True,
        "legacy_three_asset_return_ceiling_removed": True,
        "max_return_players": MAX_PLAYERS,
        "max_return_picks_with_players": MAX_PICKS,
        "max_return_total_assets_with_players": MAX_TOTAL_ASSETS,
        "max_pick_only_return_assets": MAX_PICK_ONLY,
        "expanded_packages_pruned_before_simulation": True,
        "canonical_multi_asset_package_generator_shared_component": True,
    }
