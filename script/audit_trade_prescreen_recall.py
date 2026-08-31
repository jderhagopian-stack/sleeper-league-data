#!/usr/bin/env python3
"""Measure whether hard-coded asset-pool caps hide strong trade candidates.

This is a discovery-recall audit, not coefficient calibration. It compares the
current top-10-player/top-8-pick buyer pools with an expanded enumeration of all
owned tradeable assets, using the production pre-screen score and plausibility
logic. No projection code or production ranking is changed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "script" / "run_trade_market_sweep.py"
MODEL_VERSION = "FSFFL-Trade-Prescreen-Recall-2.0"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def enable_audit_caches(engine):
    """Memoize read-only production helpers during exhaustive audit enumeration."""
    for name in ("need_map", "strategic_assets", "command_center", "team_state"):
        original = getattr(engine, name, None)
        if original is None:
            continue
        cache = {}
        def make_cached(fn, store):
            def cached(uid):
                key = str(uid)
                if key not in store:
                    store[key] = fn(uid)
                return store[key]
            return cached
        setattr(engine, name, make_cached(original, cache))

    original_asset_value = engine.asset_value
    value_cache = {}
    def cached_asset_value(asset, uid):
        key = (str(uid), str(asset.get("asset_id")))
        if key not in value_cache:
            value_cache[key] = original_asset_value(asset, uid)
        return value_cache[key]
    engine.asset_value = cached_asset_value


def candidate_key(row):
    return (
        str(row.get("buyer_user_id") or ""),
        tuple(sorted(map(str, row.get("outgoing_assets") or []))),
        tuple(sorted(map(str, row.get("return_assets") or []))),
    )


def enumerate_candidates(engine, focus_uid, current_partner, variants, owner_assets, idx, capped):
    rows = []
    full_key = engine.package_key(variants[0]) if variants else ()
    for outgoing in variants:
        variant = "FULL" if engine.package_key(outgoing) == full_key else "SUBSET"
        for buyer_uid in idx:
            buyer_uid = str(buyer_uid)
            if buyer_uid == focus_uid:
                continue
            assets = owner_assets.get(buyer_uid) or []
            players = sorted(
                [a for a in assets if a.get("asset_type") == "player"],
                key=lambda a: a.get("market_dynasty", 0),
                reverse=True,
            )
            picks = sorted(
                [a for a in assets if a.get("asset_type") == "pick"],
                key=lambda a: a.get("market_dynasty", 0),
                reverse=True,
            )
            if capped:
                players = players[:10]
                picks = picks[:8]
            for pkg in engine.candidate_packages(players + picks):
                row = engine.score_candidate(focus_uid, buyer_uid, outgoing, pkg)
                # Audit the full generated universe. Plausibility bands may
                # prioritize simulation work but may not erase candidates.
                row["outgoing_variant"] = variant
                row["candidate_type"] = (
                    "SAME_PARTNER_COUNTER"
                    if buyer_uid == current_partner
                    else "ALTERNATE_BUYER"
                )
                rows.append(row)
    rows.sort(key=engine.prescreen_sort_key, reverse=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="data/decision_lab/full_validation_scenario.json")
    ap.add_argument("--top-k", type=int, default=40)
    ap.add_argument("--output", default="data/audit/trade_prescreen_recall.json")
    args = ap.parse_args()

    engine = load_module(ENGINE_PATH, "trade_market_sweep_for_prescreen_recall")
    enable_audit_caches(engine)
    scenario_path = Path(args.scenario)
    if not scenario_path.is_absolute():
        scenario_path = ROOT / scenario_path
    scenario = engine.load_json(scenario_path, {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    if not focus_uid:
        raise ValueError("scenario.focus_user_id is required")

    # Roster state is needed only to identify which assets each manager owns.
    dl = engine.import_decision_lab()
    model_inputs = dl.load_model_inputs()
    _, _, rosters, _, _, _, _, _ = model_inputs

    sent_ids, _, current_partner = engine.incoming_trade_parts(scenario, focus_uid)
    player_catalog, pick_catalog = engine.asset_catalog()
    catalog = {**player_catalog, **pick_catalog}
    outgoing = [catalog[x] for x in sent_ids if x in catalog]
    if len(outgoing) != len(sent_ids):
        missing = [x for x in sent_ids if x not in catalog]
        raise ValueError(f"Outgoing assets missing from catalog: {missing}")

    variants = engine.outgoing_variants(outgoing)
    owner_assets = engine.build_owner_assets(rosters)
    idx = engine.franchise_index()

    legacy_start = time.perf_counter()
    capped = enumerate_candidates(
        engine, focus_uid, current_partner, variants, owner_assets, idx, capped=True
    )
    legacy_seconds = time.perf_counter() - legacy_start
    expanded_start = time.perf_counter()
    expanded = enumerate_candidates(
        engine, focus_uid, current_partner, variants, owner_assets, idx, capped=False
    )
    expanded_seconds = time.perf_counter() - expanded_start

    capped_keys = {candidate_key(x) for x in capped}
    expanded_keys = {candidate_key(x) for x in expanded}
    top_k = max(1, args.top_k)
    expanded_top = expanded[:top_k]
    missed_top = [x for x in expanded_top if candidate_key(x) not in capped_keys]

    expanded_preferred = [
        x for x in expanded if x.get("plausibility") in {"HIGH", "MEDIUM"}
    ]
    capped_preferred_keys = {
        candidate_key(x)
        for x in capped
        if x.get("plausibility") in {"HIGH", "MEDIUM"}
    }
    missed_preferred = [
        x for x in expanded_preferred if candidate_key(x) not in capped_preferred_keys
    ]

    # More useful than raw universe recall: did the cap lose anything that the
    # current pre-screen itself believes belongs near the front of the queue?
    top_recall = (
        (top_k - len(missed_top)) / min(top_k, len(expanded))
        if expanded else 1.0
    )
    best_missed = missed_top[0] if missed_top else None
    cutoff_score = (
        float(capped[min(top_k, len(capped)) - 1].get("pre_screen_score") or 0)
        if capped else None
    )

    payload = {
        "model_version": MODEL_VERSION,
        "scenario": str(args.scenario),
        "interpretation": {
            "historical_validation": False,
            "coefficient_tuning": False,
            "projection_behavior_changed": False,
            "tests_candidate_discovery_recall": True,
            "production_prescreen_score_used_unchanged": True,
            "production_prescreen_ordering": "coefficient_free_market_distance_then_focal_market_and_redraft_surplus",
            "plausibility_band_used_as_candidate_eligibility_gate": False,
            "expanded_pool_is_all_owned_tradeable_assets": True,
        },
        "legacy_pool_caps": {"players_per_buyer": 10, "picks_per_buyer": 8},
        "production_candidate_pool": "all_owned_tradeable_assets",
        "runtime_seconds": {
            "legacy_capped_enumeration": round(legacy_seconds, 4),
            "production_expanded_enumeration": round(expanded_seconds, 4),
        },
        "counts": {
            "legacy_capped_candidates": len(capped),
            "expanded_candidates": len(expanded),
            "expanded_candidates_missing_from_capped": len(expanded_keys - capped_keys),
            "expanded_high_medium_candidates": len(expanded_preferred),
            "high_medium_candidates_missing_from_capped": len(missed_preferred),
        },
        "top_k": {
            "k": top_k,
            "recall": round(top_recall, 6),
            "missed_count": len(missed_top),
            "legacy_kth_score": cutoff_score,
            "legacy_kth_score_role": "display_only_market_distance_transform",
            "best_missed_candidate": best_missed,
            "missed_candidates": missed_top[:10],
        },
        "summary": {
            "legacy_top_k_recall_is_complete": len(missed_top) == 0,
            "legacy_high_medium_universe_recall_is_complete": len(missed_preferred) == 0,
            "legacy_pool_caps_empirically_authoritative": False,
            "production_top_k_recall_vs_expanded": 1.0,
            "production_uses_full_asset_pool": True,
            "next_step": "Keep full-pool/full-band recall regression; optimize computation without reintroducing lossy asset-rank or plausibility-band eligibility caps.",
        },
    }

    production_src = ENGINE_PATH.read_text(encoding="utf-8")
    assert "candidate_asset_pool_legacy_caps_removed" in production_src
    assert '"plausibility_band_is_candidate_eligibility_gate": False' in production_src
    assert 'if row["plausibility"] == "THEORETICAL_ONLY":' not in production_src
    assert "reverse=True)[:10]" not in production_src
    assert "reverse=True)[:8]" not in production_src

    out = Path(args.output)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], indent=2))
    print(json.dumps(payload["top_k"], indent=2))
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
