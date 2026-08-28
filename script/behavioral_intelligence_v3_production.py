#!/usr/bin/env python3
"""Production facade for FSFFL Behavioral Intelligence 3.0.

The underlying implementation remains auditable in behavioral_intelligence_v3.py.
This facade is a software/operational promotion boundary. It does not claim
that BI3 predicts future manager actions or trade acceptance out of sample.

"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
RESEARCH = SCRIPT / "behavioral_intelligence_v3.py"
MODEL_VERSION = "FSFFL-Behavioral-Intelligence-3.0"
EXPECTED_RESEARCH_VERSION = "FSFFL-Behavioral-Intelligence-3.0-RESEARCH"


def load_research():
    spec = importlib.util.spec_from_file_location("bi3_validated_research", RESEARCH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    if getattr(mod, "MODEL_VERSION", None) != EXPECTED_RESEARCH_VERSION:
        raise RuntimeError(
            f"Unexpected BI3 research implementation: {getattr(mod, 'MODEL_VERSION', None)}"
        )
    return mod


def build(context_path):
    research = load_research()
    payload = research.build(context_path)
    if payload.get("model_version") != EXPECTED_RESEARCH_VERSION:
        raise RuntimeError(f"Unexpected BI3 research output: {payload.get('model_version')}")
    payload["model_version"] = MODEL_VERSION
    payload["production_status"] = "PRODUCTION"
    payload["empirical_validation_status"] = "NOT_PREDICTIVELY_VALIDATED"
    payload["predictive_holdout_validated"] = False
    payload["validation_scope"] = ["software_correctness", "boundedness", "non_leakage_and_context_normalization", "runtime_and_cache_behavior"]
    payload["source_research_model_version"] = EXPECTED_RESEARCH_VERSION
    payload["validated_research_model_version"] = EXPECTED_RESEARCH_VERSION
    payload.setdefault("architecture", {})["production_facade"] = True
    payload["architecture"]["production_promotion_preserves_research_implementation"] = True
    payload["architecture"]["software_promotion_is_not_empirical_validation"] = True
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    payload = build(args.context)
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "model_version": payload["model_version"],
        "production_status": payload["production_status"],
        "owner_count": payload.get("owner_count"),
        "league_bias_audit": payload.get("league_bias_audit"),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
