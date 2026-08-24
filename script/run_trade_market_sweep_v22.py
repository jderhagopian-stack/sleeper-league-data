#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.16 — league-realistic multi-asset package search.

Builds on 1.15 and removes the legacy compact-package ceiling from candidate
construction. FSFFL has repeatedly completed blockbuster trades with many moving
pieces, so candidate generation now allows rebuild-friendly pick-heavy packages
while retaining the 1.15 bilateral market-intelligence and negotiation-family
safeguards.

Return-package search universe:
- up to 2 players;
- up to 4 picks;
- up to 5 total incoming assets;
- pick-only packages up to 5 picks when inventory permits.

The expanded combinatorial space is still pruned before simulation by the
existing GM pre-screen, shortlist depth, buyer plausibility and bilateral gates.
Canonical state remains read-only.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V21_PATH = SCRIPT / "run_trade_market_sweep_v21.py"
V20_PATH = SCRIPT / "run_trade_market_sweep_v20.py"
V19_PATH = SCRIPT / "run_trade_market_sweep_v19.py"
V13_PATH = Path("script/run_trade_market_sweep_v13.py")
BASE_ENGINE = Path("script/run_trade_market_sweep.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.16"
MAX_PLAYERS = 2
MAX_PICKS = 4
MAX_TOTAL_ASSETS = 5
MAX_PICK_ONLY = 5


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def output_path_from_argv():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def expanded_candidate_packages(assets, max_players=MAX_PLAYERS, max_picks=MAX_PICKS):
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


def main():
    v21 = load_module(V21_PATH, "market_sweep_v21_for_v116")
    original_v21_loader = v21.load_module

    def patched_v21_loader(path: Path, name: str):
        mod = original_v21_loader(path, name)
        if Path(path) == V20_PATH:
            original_v20_loader = mod.load_module
            def patched_v20_loader(inner_path: Path, inner_name: str):
                inner = original_v20_loader(inner_path, inner_name)
                if Path(inner_path) == V19_PATH:
                    original_v19_loader = inner.load_module
                    def patched_v19_loader(deeper_path: Path, deeper_name: str):
                        deeper = original_v19_loader(deeper_path, deeper_name)
                        if Path(deeper_path) == V13_PATH:
                            original_v13_loader = deeper.load_module
                            def patched_v13_loader(base_path: Path, base_name: str):
                                engine = original_v13_loader(base_path, base_name)
                                if Path(base_path) == BASE_ENGINE:
                                    engine.candidate_packages = expanded_candidate_packages
                                return engine
                            deeper.load_module = patched_v13_loader
                        return deeper
                    inner.load_module = patched_v19_loader
                return inner
            mod.load_module = patched_v20_loader
        return mod

    v21.load_module = patched_v21_loader
    v21.MODEL_VERSION = MODEL_VERSION
    v21.main()

    output = output_path_from_argv()
    if output and output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        report["model_version"] = MODEL_VERSION
        report.setdefault("policy", {}).update({
            "league_realistic_multi_asset_search": True,
            "legacy_three_asset_return_ceiling_removed": True,
            "max_return_players": MAX_PLAYERS,
            "max_return_picks_with_players": MAX_PICKS,
            "max_return_total_assets_with_players": MAX_TOTAL_ASSETS,
            "max_pick_only_return_assets": MAX_PICK_ONLY,
            "expanded_packages_pruned_before_simulation": True,
        })
        report.setdefault("simulation", {})["execution_path"] = (
            "GM3_state_aware_plus_bilateral_market_intelligence_plus_family_dedup_plus_"
            "league_realistic_multi_asset_candidate_search"
        )
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
