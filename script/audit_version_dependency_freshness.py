#!/usr/bin/env python3
"""Enforce freshness of versioned dependencies on production-reachable paths.

Policy
------
New production code must use the newest authoritative version of a versioned
module family unless an older dependency is explicitly registered as a narrow
mechanical/compatibility dependency with a reason.

The repository already contains a historical trade-wrapper chain. Rewiring
those files wholesale would be unsafe because the versions expose different
interfaces and patch semantics. Those exact pre-existing edges are therefore
recorded as LEGACY_WRAPPER_DEBT: visible warnings that may shrink but may not
grow. Any new stale production dependency is a hard failure.

References that merely inspect or assert a version in audits/tests/metadata are
not runtime dependencies and are excluded from this graph.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = ROOT / "data" / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Version-Dependency-Freshness-Audit-1.1"

# Deliberate uses of an older module for one narrow mechanic. These are not
# endorsements of the old module's decision authority. Each should eventually
# move to a version-neutral shared utility when practical.
INTENTIONAL_MECHANICAL_PINS = {
    ("script/run_trade_market_sweep_v31.py", "script/run_trade_market_sweep_v23.py"):
        "Current v31 consumes the retained v23 dynamic-state candidate engine, then applies version-neutral historical same-state behavior, current BI3-over-BI2 behavior, candidate pools, roster resolution, roster interaction, and option governance. Historical v24-v30 wrappers are bypassed.",
}

# Exact inherited wrapper edges present when this guardrail was introduced.
# They are architectural debt, not approved design. CI permits these exact
# edges so we can refactor safely one component at a time, but any new stale
# edge fails. Removing an edge is always allowed and reduces this baseline.
LEGACY_WRAPPER_DEBT_BASELINE = {
    ("script/run_trade_market_sweep_v30.py", "script/run_trade_market_sweep_v23.py"),
    ("script/run_trade_market_sweep_v29.py", "script/run_trade_market_sweep_v28.py"),
    ("script/run_trade_market_sweep_v28.py", "script/run_trade_market_sweep_v27.py"),
    ("script/run_trade_market_sweep_v27.py", "script/run_trade_market_sweep_v26.py"),
    ("script/run_trade_market_sweep_v26.py", "script/run_trade_market_sweep_v24.py"),
    ("script/run_trade_market_sweep_v24.py", "script/run_trade_market_sweep_v23.py"),
    ("script/run_trade_market_sweep_v23.py", "script/run_trade_market_sweep_v16.py"),
    ("script/run_trade_market_sweep_v23.py", "script/run_trade_market_sweep_v18.py"),
    ("script/run_trade_market_sweep_v23.py", "script/run_trade_market_sweep_v19.py"),
    ("script/run_trade_market_sweep_v23.py", "script/run_trade_market_sweep_v20.py"),
    ("script/run_trade_market_sweep_v23.py", "script/run_trade_market_sweep_v21.py"),
    ("script/run_trade_market_sweep_v23.py", "script/run_trade_market_sweep_v22.py"),
    ("script/run_trade_market_sweep_v22.py", "script/run_trade_market_sweep_v13.py"),
    ("script/run_trade_market_sweep_v22.py", "script/run_trade_market_sweep_v19.py"),
    ("script/run_trade_market_sweep_v22.py", "script/run_trade_market_sweep_v20.py"),
    ("script/run_trade_market_sweep_v22.py", "script/run_trade_market_sweep_v21.py"),
    ("script/run_trade_market_sweep_v21.py", "script/run_trade_market_sweep_v16.py"),
    ("script/run_trade_market_sweep_v21.py", "script/run_trade_market_sweep_v18.py"),
    ("script/run_trade_market_sweep_v21.py", "script/run_trade_market_sweep_v19.py"),
    ("script/run_trade_market_sweep_v21.py", "script/run_trade_market_sweep_v20.py"),
    ("script/run_trade_market_sweep_v20.py", "script/run_trade_market_sweep_v13.py"),
    ("script/run_trade_market_sweep_v20.py", "script/run_trade_market_sweep_v18.py"),
    ("script/run_trade_market_sweep_v20.py", "script/run_trade_market_sweep_v19.py"),
    ("script/run_trade_market_sweep_v19.py", "script/run_trade_market_sweep_v13.py"),
    ("script/run_trade_market_sweep_v19.py", "script/run_trade_market_sweep_v16.py"),
    ("script/run_trade_market_sweep_v19.py", "script/run_trade_market_sweep_v18.py"),
    ("script/run_trade_market_sweep_v18.py", "script/run_trade_market_sweep_v13.py"),
    ("script/run_trade_market_sweep_v18.py", "script/run_trade_market_sweep_v16.py"),
    ("script/run_trade_market_sweep_v16.py", "script/run_trade_market_sweep_v13.py"),
}

VERSIONED_RE = re.compile(r"^(?P<prefix>.+)_v(?P<version>\d+)\.py$")
LOCAL_SCRIPT_RE = re.compile(r"(?:script/)?([A-Za-z0-9_]+(?:_v\d+)?\.py)")
EXEC_SCRIPT_RE = re.compile(r"(?:python(?:3)?|bash|sh)\s+(?:-u\s+)?script/([A-Za-z0-9_]+(?:_v\d+)?\.(?:py|sh))")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def txt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def version_families():
    families = defaultdict(list)
    for p in SCRIPT.glob("*.py"):
        m = VERSIONED_RE.match(p.name)
        if m:
            families[m.group("prefix")].append((int(m.group("version")), p))
    return families


def latest_by_family():
    out = {}
    for family, rows in version_families().items():
        version, path = max(rows, key=lambda x: x[0])
        out[family] = {"version": version, "path": rel(path)}
    return out


def references(path: Path):
    """Return runtime local-script dependencies, not metadata/audit mentions."""
    # Audit scripts inspect historical versions by design. Their string
    # references are evidence under inspection, not production model calls.
    if path.parent == SCRIPT and path.name.startswith("audit_"):
        return []

    src = txt(path)
    refs = set()

    if path.suffix in {".yml", ".yaml"}:
        # Workflow assertions may name an old model version without executing
        # it. Only explicit script execution creates a runtime dependency.
        names = EXEC_SCRIPT_RE.findall(src)
    else:
        names = LOCAL_SCRIPT_RE.findall(src)

    for name in names:
        candidate = SCRIPT / name
        if candidate.exists():
            refs.add(candidate)
    return sorted(refs)


def production_roots():
    roots = {
        SCRIPT / "run_trade_report.py",
        SCRIPT / "run_gm300_production_pipeline.sh",
        SCRIPT / "run_fsffl_gm30_counterfactual_governed.py",
        SCRIPT / "run_trade_review.py",
    }
    nonprod = ("audit", "test", "governance", "consistency", "sensitivity", "validation")
    for p in WORKFLOWS.glob("*.yml"):
        if not any(x in p.name.lower() for x in nonprod):
            roots.add(p)
    return sorted(p for p in roots if p.exists())


def reachable_graph():
    roots = production_roots()
    seen = set()
    edges = []
    q = deque(roots)
    while q:
        caller = q.popleft()
        if caller in seen:
            continue
        seen.add(caller)
        for dep in references(caller):
            edges.append((caller, dep))
            if dep not in seen:
                q.append(dep)
    return roots, seen, edges


def main():
    latest = latest_by_family()
    roots, reachable, edges = reachable_graph()
    findings = []
    stale = []

    for caller, dep in edges:
        m = VERSIONED_RE.match(dep.name)
        if not m:
            continue
        family = m.group("prefix")
        used = int(m.group("version"))
        newest = latest[family]
        if used >= newest["version"]:
            continue

        key = (rel(caller), rel(dep))
        pin_reason = INTENTIONAL_MECHANICAL_PINS.get(key)
        legacy_debt = key in LEGACY_WRAPPER_DEBT_BASELINE
        if pin_reason:
            classification = "INTENTIONAL_MECHANICAL_PIN"
            ok = True
            severity = "INFO"
            observation = "Older version is deliberately reused for a narrow mechanical dependency; it has no newer decision authority."
        elif legacy_debt:
            classification = "LEGACY_WRAPPER_DEBT"
            ok = True
            severity = "WARN"
            observation = "Pre-existing inherited wrapper dependency remains. It is frozen architecture debt and should be removed incrementally, not rewired blindly."
        else:
            classification = "NEW_UNAPPROVED_STALE_DEPENDENCY"
            ok = False
            severity = "CRITICAL"
            observation = "A new production-reachable stale dependency appeared after the architecture baseline."

        row = {
            "caller": rel(caller),
            "dependency": rel(dep),
            "family": family,
            "used_version": used,
            "latest_version": newest["version"],
            "latest_path": newest["path"],
            "classification": classification,
            "intentional_mechanical_pin": bool(pin_reason),
            "pin_reason": pin_reason,
            "legacy_wrapper_debt": legacy_debt,
        }
        stale.append(row)
        findings.append({
            "id": f"VERSION-FRESHNESS-{len(findings)+1:03d}",
            "ok": ok,
            "severity": severity,
            "observation": observation,
            **row,
        })

    edge_keys = {(rel(a), rel(b)) for a, b in edges}
    dead_pins = [
        {"caller": key[0], "dependency": key[1], "reason": reason}
        for key, reason in INTENTIONAL_MECHANICAL_PINS.items()
        if key not in edge_keys
    ]
    resolved_legacy_debt = [
        {"caller": key[0], "dependency": key[1]}
        for key in sorted(LEGACY_WRAPPER_DEBT_BASELINE)
        if key not in edge_keys
    ]

    failed = [x for x in findings if not x["ok"]]
    legacy_rows = [x for x in stale if x["classification"] == "LEGACY_WRAPPER_DEBT"]
    pin_rows = [x for x in stale if x["classification"] == "INTENTIONAL_MECHANICAL_PIN"]
    payload = {
        "model_version": MODEL_VERSION,
        "policy": {
            "new_version_requires_production_callers_to_advance": True,
            "silent_stale_dependencies_allowed": False,
            "intentional_older_mechanical_dependency_requires_explicit_pin_and_reason": True,
            "preexisting_wrapper_debt_may_shrink_but_not_grow": True,
            "metadata_assertions_are_not_runtime_dependencies": True,
            "audit_inspection_references_are_not_runtime_dependencies": True,
            "production_reachability_scanned_recursively": True,
        },
        "production_roots": [rel(x) for x in roots],
        "reachable_file_count": len(reachable),
        "versioned_families": latest,
        "stale_dependency_references": stale,
        "dead_intentional_pins": dead_pins,
        "resolved_legacy_wrapper_debt": resolved_legacy_debt,
        "summary": {
            "passed": not failed,
            "stale_reference_count": len(stale),
            "new_unapproved_stale_reference_count": len(failed),
            "legacy_wrapper_debt_count": len(legacy_rows),
            "intentional_mechanical_pin_count": len(pin_rows),
            "resolved_legacy_debt_count": len(resolved_legacy_debt),
            "dead_pin_count": len(dead_pins),
        },
        "findings": findings,
    }
    (OUT / "version_dependency_freshness_audit.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))
    if failed:
        raise SystemExit(
            "New unapproved stale production dependencies: "
            + ", ".join(f"{x['caller']} -> {x['dependency']}" for x in failed)
        )


if __name__ == "__main__":
    main()
