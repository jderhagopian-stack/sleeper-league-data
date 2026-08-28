#!/usr/bin/env python3
"""Refresh the cached production Behavioral Intelligence 3.0 profile.

This is a refresh-time job, never an interactive Market Sweep dependency. It
reconstructs historical action context using the shared historical-state provider,
builds the opportunity-normalized BI3 production profile, and persists
only the compact manager profile + manifest under data/behavioral/.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import build_behavioral_action_context as context_builder
import behavioral_intelligence_v3_production as bi3

DATA = Path("data") / "behavioral"
PROFILE = DATA / "behavioral_intelligence_v3.json"
MANIFEST = DATA / "manifest.json"


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    context = context_builder.build()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(context, tmp, sort_keys=True)
        context_path = Path(tmp.name)
    try:
        profile = bi3.build(context_path)
    finally:
        context_path.unlink(missing_ok=True)

    if profile.get("model_version") != "FSFFL-Behavioral-Intelligence-3.0":
        raise RuntimeError(f"Unexpected BI3 production profile: {profile.get('model_version')}")
    if profile.get("production_status") != "PRODUCTION":
        raise RuntimeError(f"BI3 profile is not production: {profile.get('production_status')}")

    now = datetime.now(timezone.utc).isoformat()
    profile["cache_generated_at_utc"] = now
    profile["cache_policy"] = {
        "refresh_time_only": True,
        "interactive_history_rebuild": False,
        "market_sweep_reads_compact_cache_only": True,
    }
    PROFILE.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")

    manifest = {
        "generated_at_utc": now,
        "profile_path": str(PROFILE),
        "profile_model_version": profile.get("model_version"),
        "production_status": profile.get("production_status"),
        "validated_research_model_version": profile.get("validated_research_model_version"),
        "source_research_model_version": profile.get("source_research_model_version"),
        "empirical_validation_status": profile.get("empirical_validation_status"),
        "predictive_holdout_validated": profile.get("predictive_holdout_validated"),
        "action_context_model_version": profile.get("action_context_model_version"),
        "historical_state_provider": profile.get("historical_state_provider"),
        "owner_count": profile.get("owner_count"),
        "league_bias_audit": profile.get("league_bias_audit"),
        "interactive_history_rebuild": False,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
