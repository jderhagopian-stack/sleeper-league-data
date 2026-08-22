#!/usr/bin/env python3
"""Validate the frozen GM 3.0.0 reference contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "gm30_reference"
MANIFEST = REF / "manifest.json"


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("release") != "GM-3.0.0":
        raise SystemExit("Unexpected frozen release identifier")
    if manifest.get("status") != "FROZEN_PRODUCTION_BASELINE":
        raise SystemExit("GM 3.0.0 reference is not marked frozen")

    snapshot_map = {
        "validation_report.json": "data/gm/validation_report.json",
        "franchise_index.json": "data/gm/franchise_index.json",
        "gm30_prospect_radar.json": "data/gm/gm30_prospect_radar.json",
        "calibration_report.json": "data/gm/calibration_report.json",
        "breakout_calibration.json": "data/gm/breakout_calibration.json",
        "emerging_value.json": "data/gm/emerging_value.json",
        "gm_manifest.json": "data/gm/manifest.json",
        "prospect_feature_audit.json": "data/gm3_prospect_feature_audit.json",
    }

    for frozen_name, source_path in snapshot_map.items():
        frozen = REF / "snapshot" / frozen_name
        if not frozen.exists():
            raise SystemExit(f"Missing frozen snapshot file: {frozen}")
        expected = manifest["frozen_outputs"][source_path]
        actual = git_blob_sha(frozen)
        if actual != expected:
            raise SystemExit(
                f"Frozen snapshot drift: {frozen_name}: expected {expected}, got {actual}"
            )

    # This deliberately validates the frozen reference, not the live files.
    # Live GM 3.x code may evolve; the manifest preserves the exact 3.0.0 SHAs.
    validation = json.loads((REF / "snapshot" / "validation_report.json").read_text())
    if not validation.get("passed"):
        raise SystemExit("Frozen GM 3.0 validation report is not passing")
    if validation.get("warnings"):
        raise SystemExit("Frozen GM 3.0 validation report contains warnings")
    sim = validation.get("simulator_validation") or {}
    if not sim.get("validation_passed"):
        raise SystemExit("Frozen GM 3.0 simulator validation is not passing")

    prospect = json.loads((REF / "snapshot" / "gm30_prospect_radar.json").read_text())
    if prospect.get("prospect_count") != 47:
        raise SystemExit("Frozen prospect count changed unexpectedly")
    if prospect.get("signal_eligible_count", 0) < 46:
        raise SystemExit("Frozen prospect intelligence coverage is below baseline")

    print("GM 3.0.0 frozen reference: PASS")
    print(f"Source commit: {manifest['source_commit']}")
    print("Snapshot integrity: PASS")
    print("GM validation: PASS")
    print("Simulator validation: PASS")
    print("Prospect intelligence baseline: PASS")


if __name__ == "__main__":
    main()
