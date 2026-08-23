#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.4.

Performance layer over 1.3. The candidate generator repeatedly queries the same
GM 3.0 franchise documents while enumerating thousands of packages. 1.4 memoizes
those read-only lookups for the life of a single market-sweep process, preserving
1.3 decision logic and exact DP lineup optimization while eliminating redundant
JSON reads and parsing.

Canonical Sleeper / GM / Simulator state remains read-only.
"""

from __future__ import annotations

import functools
import importlib.util
from pathlib import Path

V13_PATH = Path("script/run_trade_market_sweep_v13.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.4"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def install_read_caches(engine):
    """Memoize immutable/read-only GM data accessors for one process run."""
    engine.franchise_index = functools.lru_cache(maxsize=1)(engine.franchise_index)
    engine.asset_catalog = functools.lru_cache(maxsize=1)(engine.asset_catalog)
    engine.command_center = functools.lru_cache(maxsize=None)(engine.command_center)
    engine.strategic_assets = functools.lru_cache(maxsize=None)(engine.strategic_assets)
    engine.need_map = functools.lru_cache(maxsize=None)(engine.need_map)
    engine.team_state = functools.lru_cache(maxsize=None)(engine.team_state)
    return engine


def main():
    v13 = load_module(V13_PATH, "market_sweep_v13_for_v14")
    original_loader = v13.load_module

    def cached_loader(path: Path, name: str):
        mod = original_loader(path, name)
        if Path(path) == v13.BASE_ENGINE:
            install_read_caches(mod)
        return mod

    v13.load_module = cached_loader
    v13.MODEL_VERSION = MODEL_VERSION
    v13.main()


if __name__ == "__main__":
    main()
