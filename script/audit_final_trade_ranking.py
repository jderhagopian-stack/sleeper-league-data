#!/usr/bin/env python3
"""Governance audit for the final trade-negotiation ranking formula."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"; DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
MODEL_VERSION="FSFFL-Final-Trade-Ranking-Governance-1.1"

def read(name): return (SCRIPT/name).read_text(encoding="utf-8")
def compact(s): return ''.join(str(s).split())

def uses_canonical_composer(src):
    c=compact(src)
    return (
        'negotiation_ranking.py' in src
        and 'nr.compose(strategic,acceptance,behavior)' in c
    )

def main():
    ranker=read("negotiation_ranking.py")
    v18=read("run_trade_market_sweep_v18.py")
    v20=read("run_trade_market_sweep_v20.py")
    v23=read("run_trade_market_sweep_v23.py")
    v30=read("run_trade_market_sweep_v30.py")

    canonical_weights=(
        "STRATEGIC_WEIGHT = 0.625" in ranker
        and "ACCEPTANCE_WEIGHT = 0.375" in ranker
        and "OWNER_BEHAVIOR_WEIGHT = 0.0" in ranker
    )
    ratio_preserved=abs((0.625/0.375)-(0.50/0.30)) < 1e-12
    composer_paths={
        'v18':uses_canonical_composer(v18),
        'v20':uses_canonical_composer(v20),
        'v23':uses_canonical_composer(v23),
    }
    shared_composer=all(composer_paths.values())
    production_refresh_uses_v23=(
        "run_trade_market_sweep_v23.py" in v30
        and "recompute_negotiation_ranking" in v30
    )
    diagnostic_retained=(
        '"owner_behavior_match_component"' in ranker
        and '"owner_behavior_component_is_diagnostic_only": True' in ranker
    )
    duplicate_positive_behavior_weight=(
        ".20 * behavior" in v20 or ".20 * behavior" in v23
        or "OWNER_BEHAVIOR_WEIGHT = 0.2" in ranker
    )

    findings=[
      {
        "id":"FINAL-RANK-BEHAVIOR-DEDUP-001",
        "severity":"INFO" if canonical_weights and shared_composer else "CRITICAL",
        "status":"STRUCTURAL_DOUBLE_COUNT_REMOVED" if canonical_weights and shared_composer else "DOUBLE_COUNT_OR_MULTIPLE_SOURCE_REMAINS",
        "observation":"Owner-behavior evidence already modifies acceptance fit upstream. The canonical negotiation composer therefore assigns the separate behavior diagnostic zero incremental ranking weight. The original strategic:acceptance ratio is preserved exactly by renormalizing 0.50:0.30 to 0.625:0.375.",
        "production_behavior_changed":True,
        "change_basis":"structural de-duplication; no empirical coefficient was learned or tuned",
      },
      {
        "id":"FINAL-RANK-SOURCE-OF-TRUTH-001",
        "severity":"INFO" if shared_composer and production_refresh_uses_v23 else "HIGH",
        "status":"CANONICAL_COMPOSER" if shared_composer and production_refresh_uses_v23 else "MULTIPLE_FORMULAS_REMAIN",
        "observation":"Legacy, state-aware and post-overlay ranking paths share the same negotiation_ranking.py composer for final strategic/acceptance weighting while retaining their path-specific strategic component construction. The audit normalizes source whitespace so formatting cannot create a false failure.",
        "canonical_composer_by_path":composer_paths,
      },
      {
        "id":"FINAL-RANK-EMPIRICAL-001",
        "severity":"HIGH",
        "status":"WEIGHTS_STILL_PROVISIONAL",
        "observation":"Removing duplicate evidence does not empirically validate the remaining strategic/acceptance tradeoff. The 0.625/0.375 weights inherit the prior 0.50/0.30 distinct-component ratio and remain provisional pending a defensible historical/choice target.",
        "authoritative_empirical_claim_allowed":False,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "production_behavior_changed":True,
      "policy":{
        "duplicate_evidence_must_not_receive_positive_incremental_weight":True,
        "deduplication_preserves_prior_distinct_component_ratio":True,
        "owner_behavior_diagnostic_preserved":True,
        "deduplication_is_not_empirical_validation":True,
        "remaining_ranking_weights_remain_provisional":True,
        "source_formatting_cannot_determine_governance_pass_fail":True,
      },
      "summary":{
        "canonical_weights_detected":canonical_weights,
        "prior_strategic_acceptance_ratio_preserved":ratio_preserved,
        "shared_composer_used_by_v18_v20_v23":shared_composer,
        "canonical_composer_by_path":composer_paths,
        "production_post_overlay_refresh_uses_v23":production_refresh_uses_v23,
        "owner_behavior_diagnostic_retained":diagnostic_retained,
        "positive_duplicate_behavior_weight_detected":duplicate_positive_behavior_weight,
      },
      "findings":findings,
    }
    (OUT/"final_trade_ranking_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not canonical_weights or not ratio_preserved: raise SystemExit("Canonical ranking weights do not implement exact structural de-duplication")
    if not shared_composer or not production_refresh_uses_v23: raise SystemExit("Final negotiation ranking still has multiple weighting sources")
    if duplicate_positive_behavior_weight: raise SystemExit("Positive duplicate owner-behavior ranking weight remains")
if __name__=="__main__": main()
