#!/usr/bin/env python3
"""Static release-readiness guardrails for Alternate History CI.

This validator deliberately does not execute model logic. It protects the
consolidated validation topology so future changes cannot silently restore the
large PR-workflow fan-out that previously duplicated expensive historical
replay. It also enforces the explicit opt-in boundary between normal GM 3.0
execution and Alternate History review/what-if execution.
"""

from __future__ import annotations

from pathlib import Path

from validate_gm30_alternate_history_boundary import validate as validate_integration_boundary

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PREFIX = "alternate-history-"
AUTOMATIC = {
    "alternate-history-validation.yml",
    "alternate-history-generic-cycle-validation.yml",
    "alternate-history-end-to-end-validation.yml",
}
MANUAL_REGRESSIONS = {
    "alternate-history-historical-anchor-validation.yml",
    "alternate-history-maxpf-validation.yml",
    "alternate-history-next-draft-validation.yml",
    "alternate-history-performance-audit.yml",
    "alternate-history-predraft-validation.yml",
    "alternate-history-present-day-validation.yml",
    "alternate-history-rookie-draft-validation.yml",
    "alternate-history-season-boundary-validation.yml",
    "alternate-history-season-feedback-validation.yml",
    "alternate-history-second-season-validation.yml",
    "alternate-history-third-season-validation.yml",
    "alternate-history-weighted-outlook-validation.yml",
}


def _has_pull_request_trigger(text: str) -> bool:
    return any(line.strip() == "pull_request:" for line in text.splitlines())


def _has_dispatch_trigger(text: str) -> bool:
    return any(line.strip() == "workflow_dispatch:" for line in text.splitlines())


def _require_runtime_guards(name: str, text: str) -> list[str]:
    failures = []
    if "cancel-in-progress: true" not in text:
        failures.append(f"{name}: missing stale-run cancellation")
    if "timeout-minutes:" not in text:
        failures.append(f"{name}: missing job timeout")
    return failures


def validate() -> None:
    # First prove that normal GM 3.0 has no implicit path into Alternate History
    # and that the historical information firewall remains explicit.
    validate_integration_boundary()

    workflow_paths = sorted(WORKFLOW_DIR.glob(f"{PREFIX}*.yml"))
    found = {path.name for path in workflow_paths}
    failures: list[str] = []

    missing = (AUTOMATIC | MANUAL_REGRESSIONS) - found
    if missing:
        failures.append(f"missing expected Alternate History workflows: {sorted(missing)}")

    automatic_found = set()
    for path in workflow_paths:
        text = path.read_text()
        is_pr = _has_pull_request_trigger(text)
        is_dispatch = _has_dispatch_trigger(text)

        if is_pr:
            automatic_found.add(path.name)
            if path.name not in AUTOMATIC:
                failures.append(
                    f"{path.name}: unexpected pull_request trigger would restore CI fan-out"
                )
        elif path.name in AUTOMATIC:
            failures.append(f"{path.name}: required consolidated PR trigger is missing")

        if path.name in MANUAL_REGRESSIONS:
            if is_pr:
                failures.append(f"{path.name}: stage regression must remain manual-only")
            if not is_dispatch:
                failures.append(f"{path.name}: manual regression lacks workflow_dispatch")

        if path.name in AUTOMATIC:
            failures.extend(_require_runtime_guards(path.name, text))

    if automatic_found != AUTOMATIC:
        failures.append(
            "automatic Alternate History PR workflow set changed: "
            f"expected={sorted(AUTOMATIC)} actual={sorted(automatic_found)}"
        )

    if failures:
        raise SystemExit("Alternate History release-readiness validation failed:\n- " + "\n- ".join(failures))

    print(
        "Alternate History release-readiness validation passed: "
        f"{len(AUTOMATIC)} consolidated PR gates, "
        f"{len(MANUAL_REGRESSIONS)} manual regressions, runtime guards present, "
        "GM 3.0 opt-in boundary intact."
    )


if __name__ == "__main__":
    validate()
