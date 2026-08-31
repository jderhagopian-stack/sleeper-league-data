#!/usr/bin/env python3
"""Architecture guardrail for the FSFFL Opportunity Engine."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "script"
OUT = ROOT / "data" / "audit"
OUT.mkdir(parents=True, exist_ok=True)

APP = SCRIPT / "opportunity_engine" / "application.py"
CONTRACT = ROOT / "data" / "model_governance" / "opportunity_engine_architecture.json"
ARCH = ROOT / "data" / "model_governance" / "application_architecture.json"

source = APP.read_text(encoding="utf-8")
contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
arch = json.loads(ARCH.read_text(encoding="utf-8"))
declared = ((arch.get("applications") or {}).get("opportunity_engine") or {})

findings = [
    {
        "id": "OE-LAYER-001",
        "ok": contract.get("layer") == "Application" and declared.get("entrypoint") == "script/opportunity_engine/application.py",
        "observation": "Opportunity Engine must remain an Application-layer orchestrator.",
    },
    {
        "id": "OE-GM3-002",
        "ok": 'TEAM_IMPROVEMENT = SCRIPT / "gm3" / "team_improvement.py"' in source,
        "observation": "Phase 1 candidate discovery must enter through the stable GM3 Team Improvement facade.",
    },
    {
        "id": "OE-NO-RESCORE-003",
        "ok": (
            '"opportunity_engine_rescoring_applied": False' in source
            and '"opportunity_engine_reranking_applied": False' in source
            and "team_improvement_score =" not in source
        ),
        "observation": "Opportunity Engine may compose governed outputs but may not create a parallel score.",
    },
    {
        "id": "OE-TRADE-AUTHORITY-004",
        "ok": (
            declared.get("authoritative_consumers", {}).get("trade_execution_review") == "script/trade_engine.py"
            and "CANDIDATE_REQUIRES_TRADE_DECISION_REVIEW" in source
        ),
        "observation": "Trade discovery does not replace Trade Decision execution/recommendation authority.",
    },
    {
        "id": "OE-NO-LEGACY-005",
        "ok": all(x not in source for x in [
            "build_fsffl_gm_engine",
            "run_trade_market_sweep_v",
            "run_fsffl_season_simulator_preproduction",
            "decision_lab_state_aware",
        ]),
        "observation": "The new application may not directly bind to legacy or implementation-version authorities.",
    },
    {
        "id": "OE-SHARED-CORE-006",
        "ok": declared.get("shared_core_policy", "").startswith("No new Shared Core primitive"),
        "observation": "Phase 1 does not prematurely promote application orchestration into Shared Core.",
    },
]

failed = [x for x in findings if not x["ok"]]
payload = {
    "model_version": "FSFFL-Opportunity-Engine-Architecture-Audit-1.0",
    "summary": {"passed": not failed, "finding_count": len(findings), "failure_count": len(failed)},
    "findings": findings,
}
path = OUT / "opportunity_engine_architecture_audit.json"
path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(json.dumps(payload, indent=2))
if failed:
    raise SystemExit("Opportunity Engine architecture audit failed: " + ", ".join(x["id"] for x in failed))
