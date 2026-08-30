#!/usr/bin/env python3
"""Equivalence audit for shared multi-asset package generation.

Migration-safety test only: prove the version-neutral generator enumerates the
same package universe as current production v22 across representative asset
inventories and caller-supplied player/pick caps.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def keys(packages):
    return [
        tuple(sorted(str(a.get("asset_id")) for a in pkg))
        for pkg in packages
    ]


def fixture(players, picks):
    out = [
        {"asset_id": f"player:{i}", "asset_type": "player"}
        for i in range(1, players + 1)
    ]
    out += [
        {"asset_id": f"pick:202{7+i}:R{1+(i%3)}:orig{i}", "asset_type": "pick"}
        for i in range(1, picks + 1)
    ]
    return out


def main():
    old = load(SCRIPT / "run_trade_market_sweep_v22.py", "v22_package_reference")
    new = load(SCRIPT / "trade_multi_asset_packages.py", "shared_package_generator")

    cases = [
        (0, 1, old.MAX_PLAYERS, old.MAX_PICKS),
        (0, 5, old.MAX_PLAYERS, old.MAX_PICKS),
        (0, 6, old.MAX_PLAYERS, old.MAX_PICKS),
        (1, 4, old.MAX_PLAYERS, old.MAX_PICKS),
        (2, 4, old.MAX_PLAYERS, old.MAX_PICKS),
        (3, 6, old.MAX_PLAYERS, old.MAX_PICKS),
        (3, 6, 1, 2),
        (2, 6, 2, 1),
    ]

    for i, (np, nk, max_players, max_picks) in enumerate(cases):
        assets = fixture(np, nk)
        old_keys = keys(old.expanded_candidate_packages(
            assets, max_players=max_players, max_picks=max_picks
        ))
        new_keys = keys(new.candidate_packages(
            assets, max_players=max_players, max_picks=max_picks
        ))
        if old_keys != new_keys:
            raise AssertionError(
                f"case {i} mismatch\nOLD={old_keys!r}\nNEW={new_keys!r}"
            )

    # Explicit contract assertions.
    five_picks = list(new.candidate_packages(fixture(0, 5)))
    assert any(len(pkg) == 5 for pkg in five_picks)
    mixed = list(new.candidate_packages(fixture(2, 6)))
    assert all(
        len(pkg) <= new.MAX_TOTAL_ASSETS
        for pkg in mixed
        if any(a.get("asset_type") == "player" for a in pkg)
    )
    assert all(
        sum(a.get("asset_type") == "player" for a in pkg) <= new.MAX_PLAYERS
        for pkg in mixed
    )

    print({
        "status": "PASS",
        "shared_model_version": new.MODEL_VERSION,
        "case_count": len(cases),
        "five_pick_package_supported": True,
        "production_switched": False,
    })


if __name__ == "__main__":
    main()
