#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.15 — bilateral realism and negotiation-family diversity.

Builds on Market Sweep 1.14. Production safeguards:
1) acceptance fit ranks negotiation realism but does not decide candidate eligibility;
2) strong negative buyer utility at the current franchise state is a hard bilateral gate;
3) near-duplicate negotiation families (same buyer, same focal outgoing assets,
   same primary incoming players with only marginal pick variation) occupy one slot.

The reserved swing slot must be a genuinely distinct negotiation family and can
never drive the primary action. Canonical state remains read-only.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V20_PATH = SCRIPT / "run_trade_market_sweep_v20.py"
V19_PATH = SCRIPT / "run_trade_market_sweep_v19.py"
V16_PATH = Path("script/run_trade_market_sweep_v16.py")
V18_PATH = Path("script/run_trade_market_sweep_v18.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.15"
MAX_NORMAL_OPTIONS_PER_BUYER = 2


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def output_path_from_argv():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def _is_pick(asset_id: str) -> bool:
    return str(asset_id).startswith("pick:")


def negotiation_family_key(row):
    """Collapse trivial pick sweetener variants of the same core negotiation."""
    buyer = str(row.get("buyer_user_id") or "")
    outgoing = tuple(sorted(str(x) for x in (row.get("outgoing_assets") or [])))
    returns = [str(x) for x in (row.get("return_assets") or [])]
    primary_players = tuple(sorted(x for x in returns if not _is_pick(x)))
    pick_only = tuple(sorted(x for x in returns if _is_pick(x))) if not primary_players else ()
    return buyer, outgoing, primary_players, pick_only


def _buyer_hard_gate(br):
    state = str(br.get("buyer_state") or "unknown")
    title = sf(br.get("buyer_title_delta"))
    dynasty = sf(br.get("buyer_market_dynasty_delta"))
    redraft = sf(br.get("buyer_market_redraft_delta"))
    break_glass = sf(br.get("buyer_break_glass_delta"))

    fail = False
    reason = None
    if state == "elite_contender" and title <= -0.03 and dynasty < 0 and break_glass < 0:
        fail = True; reason = "elite contender loses title equity plus dynasty and break-glass value"
    elif state == "contender" and title <= -0.04 and dynasty < 0 and break_glass < 0:
        fail = True; reason = "contender loses meaningful title equity plus dynasty and break-glass value"
    elif state == "retool" and dynasty <= -1200 and break_glass <= -1200:
        fail = True; reason = "retool buyer gives up excessive long-term and break-glass value"
    elif state == "rebuild" and dynasty <= -900 and break_glass <= -900:
        fail = True; reason = "rebuild buyer gives up excessive long-term and break-glass value"

    if dynasty <= -1400 and redraft <= -1800 and break_glass <= -1200:
        fail = True; reason = "buyer loses heavily across dynasty, redraft, and break-glass value"
    return (not fail), reason


def patch_v16(mod):
    original = mod.buyer_rationality
    def buyer_rationality(row, dl):
        br = original(row, dl)
        passes, reason = _buyer_hard_gate(br)
        br["market_intelligence_hard_gate_pass"] = bool(passes)
        br["market_intelligence_hard_gate_reason"] = reason or "buyer current-state utility clears bilateral hard gate"
        if not passes:
            br["current_state_viable"] = False
            br["current_state_gate"] = "BUYER_IRRATIONAL"
            br["reason"] = reason
            br["heuristic_acceptance_fit_score"] = min(sf(br.get("heuristic_acceptance_fit_score")), 0.27)
            br["heuristic_acceptance_fit"] = "VERY_LOW"
        return br
    mod.buyer_rationality = buyer_rationality
    return mod


def select_swing_distinct(viable):
    if not viable:
        return None
    ambitious = [r for r in viable if r.get("acceptance_likelihood") in {"LOW", "VERY_LOW"}]
    pool = ambitious or viable
    return max(pool, key=lambda r: (
        sf((r.get("negotiation_ranking") or {}).get("focal_strategic_gain_component")),
        sf(r.get("post_sim_score")),
        sf((r.get("negotiation_ranking") or {}).get("score")),
    ))


def select_normal_four_strict(viable, swing):
    """Normal slots use rational candidates from distinct negotiation families.

    Acceptance fit remains inside negotiation ranking, so more realistic deals
    naturally sort higher. The uncalibrated HIGH/MEDIUM labels do not veto an
    otherwise rational candidate.
    """
    selected = []
    counts = Counter()
    used_families = set()
    swing_family = negotiation_family_key(swing) if swing else None

    for row in viable:
        fam = negotiation_family_key(row)
        if swing_family and fam == swing_family:
            continue
        if fam in used_families:
            continue
        uid = str(row.get("buyer_user_id") or "")
        if counts[uid] >= MAX_NORMAL_OPTIONS_PER_BUYER:
            continue
        selected.append(row)
        used_families.add(fam)
        counts[uid] += 1
        if len(selected) == 4:
            break
    return selected


def main():
    v20 = load_module(V20_PATH, "market_sweep_v20_for_v115")
    original_v20_loader = v20.load_module

    def patched_v20_loader(path: Path, name: str):
        mod = original_v20_loader(path, name)
        if Path(path) == V19_PATH:
            original_v19_loader = mod.load_module
            def patched_v19_loader(inner_path: Path, inner_name: str):
                inner = original_v19_loader(inner_path, inner_name)
                if Path(inner_path) == V16_PATH:
                    inner = patch_v16(inner)
                return inner
            mod.load_module = patched_v19_loader
            mod.select_swing = select_swing_distinct
            mod.select_normal_four = select_normal_four_strict
        return mod

    v20.load_module = patched_v20_loader
    v20.MODEL_VERSION = MODEL_VERSION
    v20.main()

    output = output_path_from_argv()
    if output and output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        report["model_version"] = MODEL_VERSION
        top = report.get("top_5_alternatives") or []
        families = [negotiation_family_key(r) for r in top]
        report.setdefault("policy", {}).update({
            "acceptance_band_is_ranking_signal_not_eligibility_gate": True,
            "market_intelligence_can_veto_buyer_current_state_viability": True,
            "negotiation_family_deduplication": True,
            "swing_must_be_distinct_negotiation_family": True,
            "low_and_very_low_acceptance_fit_can_appear_in_normal_slots_if_bilaterally_rational": True,
        })
        report.setdefault("candidate_counts", {})["top_five_unique_negotiation_families"] = len(set(families))
        report.setdefault("simulation", {})["execution_path"] = (
            "GM3_continuous_state_weights_plus_post_trade_profile_recompute_plus_"
            "state_aware_negotiation_ranking_plus_bilateral_market_intelligence_gate_plus_family_dedup"
        )
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
