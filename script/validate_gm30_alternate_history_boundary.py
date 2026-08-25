#!/usr/bin/env python3
"""Validate the hard opt-in boundary between GM 3.0 and Alternate History.

This is a static architecture guard. It intentionally does not run either
model. Its job is to prevent a future refactor from silently making historical
replay part of normal GM 3.0 execution or allowing GM 3.0 values to leak into
historical decisions.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "data" / "alternate_history" / "integration_contract.json"
GENERIC_AH = ROOT / "script" / "run_fsffl_generic_alternate_history.py"

FORBIDDEN_GM_IMPORT_PREFIXES = (
    "alternate_history_",
    "run_fsffl_generic_alternate_history",
    "run_fsffl_alternate_history",
    "run_fsffl_multiseason_",
    "run_fsffl_historical_",
)


def _python_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _forbidden_import(module: str) -> bool:
    leaf = module.rsplit(".", 1)[-1]
    return any(leaf.startswith(prefix) for prefix in FORBIDDEN_GM_IMPORT_PREFIXES)


def validate() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []

    if contract.get("integration_mode") != "EXPLICIT_OPT_IN_ONLY":
        failures.append("integration contract must remain EXPLICIT_OPT_IN_ONLY")
    if contract.get("normal_gm30_behavior_must_remain_unchanged") is not True:
        failures.append("normal GM 3.0 behavior must remain explicitly unchanged")

    adapter = ROOT / str(contract.get("alternate_history_entrypoint") or "")
    if not adapter.exists():
        failures.append(f"explicit Alternate History adapter is missing: {adapter}")

    # Normal GM 3.0 Python entry points may not import Alternate History at all.
    for rel in contract.get("normal_gm30_entrypoints") or []:
        path = ROOT / rel
        if not path.exists():
            failures.append(f"normal GM 3.0 entry point missing: {rel}")
            continue
        if path.suffix != ".py":
            text = path.read_text(encoding="utf-8")
            if "alternate_history" in text or "run_gm30_alternate_history_review" in text:
                failures.append(f"{rel}: normal GM pipeline references Alternate History")
            continue
        for module in sorted(_python_imports(path)):
            if _forbidden_import(module):
                failures.append(f"{rel}: forbidden implicit Alternate History import: {module}")
        text = path.read_text(encoding="utf-8")
        if "run_gm30_alternate_history_review" in text:
            failures.append(f"{rel}: explicit review adapter may not be called by normal GM execution")

    # The historical orchestrator itself must keep the no-current-GM-value firewall
    # as an executable/reportable invariant.
    generic_text = GENERIC_AH.read_text(encoding="utf-8")
    required_fragments = (
        '"current_gm3_numeric_values_used_for_historical_decisions": False',
        '"completed_nfl_history_is_immutable": True',
        '"active-season fork orchestration is a separate live what-if command surface"',
    )
    for fragment in required_fragments:
        if fragment not in generic_text:
            failures.append(f"generic Alternate History firewall invariant missing: {fragment}")

    firewall = contract.get("information_firewall") or {}
    for key in (
        "completed_nfl_history_immutable",
        "current_gm3_numeric_values_may_not_drive_historical_decisions",
        "alternate_history_outputs_may_not_mutate_gm30_canonical_outputs",
        "alternate_history_results_are_request_scoped",
    ):
        if firewall.get(key) is not True:
            failures.append(f"integration firewall contract weakened: {key}")

    if failures:
        raise SystemExit(
            "GM 3.0 / Alternate History boundary validation failed:\n- "
            + "\n- ".join(failures)
        )

    print(
        "GM 3.0 / Alternate History boundary validation passed: "
        "normal GM entrypoints are isolated; historical replay is explicit opt-in only."
    )


if __name__ == "__main__":
    validate()
