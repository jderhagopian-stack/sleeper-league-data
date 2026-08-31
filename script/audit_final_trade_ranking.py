#!/usr/bin/env python3
"""Governance audit for the final trade-negotiation ranking formula."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"; DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
MODEL_VERSION="FSFFL-Final-Trade-Ranking-Governance-2.0"

def read(name): return (SCRIPT/name).read_text(encoding="utf-8")
def compact(s): return ''.join(str(s).split())

def uses_canonical_composer(src):
    c=compact(src)
    return (
        'negotiation_ranking.py' in src
        and (
            'nr.compose(strategic,acceptance,behavior)' in c
            or 'nr.recompute_from_row(row)' in c
        )
    )

def main():
    ranker=read("negotiation_ranking.py")
    v18=read("run_trade_market_sweep_v18.py")
    v20=read("run_trade_market_sweep_v20.py")
    v23=read("run_trade_market_sweep_v23.py")
    v30=read("run_trade_market_sweep_v30.py")

    canonical_weights=(
        "STRATEGIC_WEIGHT = 1.0" in ranker
        and "ACCEPTANCE_WEIGHT = 0.0" in ranker
        and "OWNER_BEHAVIOR_WEIGHT = 0.0" in ranker
        and '"arbitrary_strategic_acceptance_exchange_rate_authorized": False' in ranker
    )
    ratio_preserved=False
    arbitrary_exchange_rate_removed=canonical_weights
    composer_paths={
        'v18':uses_canonical_composer(v18),
        'v20':uses_canonical_composer(v20),
        'v23':uses_canonical_composer(v23),
    }
    shared_composer=all(composer_paths.values())
    production_refresh_uses_shared=(
        "negotiation_ranking.py" in v30
        and "recompute_from_row" in v30
        and "run_trade_market_sweep_v23.py" not in v30
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
        "observation":"Counterparty feasibility is handled by shared buyer utility and acceptance fit remains descriptive. The canonical negotiation rank therefore orders viable candidates by focal decision utility alone; acceptance and owner behavior receive zero arbitrary exchange weight.",
        "production_behavior_changed":True,
        "change_basis":"structural de-duplication; no empirical coefficient was learned or tuned",
      },
      {
        "id":"FINAL-RANK-SOURCE-OF-TRUTH-001",
        "severity":"INFO" if shared_composer and production_refresh_uses_shared else "HIGH",
        "status":"CANONICAL_COMPOSER" if shared_composer and production_refresh_uses_shared else "MULTIPLE_FORMULAS_REMAIN",
        "observation":"Legacy and state-aware paths share the same negotiation_ranking.py composer, and the post-overlay production refresh now calls the version-neutral row-level helper directly rather than importing a superseded trade wrapper. Path-specific strategic construction is retained. The audit normalizes source whitespace so formatting cannot create a false failure.",
        "canonical_composer_by_path":composer_paths,
        "production_post_overlay_refresh_uses_shared_helper":production_refresh_uses_shared,
      },
      {
        "id":"FINAL-RANK-EMPIRICAL-001",
        "severity":"HIGH",
        "status":"ARBITRARY_STRATEGIC_ACCEPTANCE_EXCHANGE_RATE_REMOVED",
        "observation":"No fitted acceptance probability exists, so acceptance is not traded off numerically against focal utility. If future accepted/rejected opportunity data support a calibrated ranking contribution, it can be introduced with held-out validation.",
        "authoritative_empirical_claim_allowed":False,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "production_behavior_changed":True,
      "policy":{
        "duplicate_evidence_must_not_receive_positive_incremental_weight":True,
        "deduplication_preserves_prior_distinct_component_ratio":False,
        "arbitrary_strategic_acceptance_exchange_rate_removed":True,
        "owner_behavior_diagnostic_preserved":True,
        "deduplication_is_not_empirical_validation":True,
        "acceptance_ranking_weight_authorized":False,
        "source_formatting_cannot_determine_governance_pass_fail":True,
        "production_refresh_should_use_version_neutral_shared_helper":True,
      },
      "summary":{
        "canonical_weights_detected":canonical_weights,
        "prior_strategic_acceptance_ratio_preserved":ratio_preserved,
        "arbitrary_strategic_acceptance_exchange_rate_removed":arbitrary_exchange_rate_removed,
        "shared_composer_used_by_v18_v20_v23":shared_composer,
        "canonical_composer_by_path":composer_paths,
        "production_post_overlay_refresh_uses_v23":False,
        "production_post_overlay_refresh_uses_shared_helper":production_refresh_uses_shared,
        "owner_behavior_diagnostic_retained":diagnostic_retained,
        "positive_duplicate_behavior_weight_detected":duplicate_positive_behavior_weight,
      },
      "findings":findings,
    }
    (OUT/"final_trade_ranking_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not canonical_weights or not arbitrary_exchange_rate_removed: raise SystemExit("Canonical ranking still contains unsupported strategic/acceptance exchange weighting")
    if not shared_composer or not production_refresh_uses_shared: raise SystemExit("Final negotiation ranking does not use the canonical shared source")
    if duplicate_positive_behavior_weight: raise SystemExit("Positive duplicate owner-behavior ranking weight remains")
if __name__=="__main__": main()
