#!/usr/bin/env python3
"""Enforce freshness of versioned dependencies on production-reachable paths.

Policy
------
When a newer version of a versioned module family is published, production code
must reference the newest version unless an older dependency is explicitly
registered as an intentional compatibility/mechanical dependency with a reason.

This prevents silent stale dependencies while still allowing deliberate wrapper
composition (for example, a newest wrapper may intentionally build on the prior
wrapper's mechanics).
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

MODEL_VERSION = "FSFFL-Version-Dependency-Freshness-Audit-1.0"

# A stale version reference is permitted only when it is a deliberate internal
# composition dependency. Every exception must identify the exact caller,
# dependency and rationale. New exceptions therefore require code review.
INTENTIONAL_PINS = {
    ("script/run_trade_market_sweep_v31.py", "script/run_trade_market_sweep_v30.py"):
        "v31 is the authoritative outcome-governance wrapper and intentionally consumes v30 mechanics before replacing v30 option comparisons and final action authority.",
    ("script/run_trade_market_sweep_v30.py", "script/run_trade_market_sweep_v29.py"):
        "v30 adds roster-interaction mechanics on top of the retained v29 candidate/simulation layer; v30 is not final production decision authority.",
    ("script/run_trade_review.py", "script/run_trade_market_sweep_v13.py"):
        "Retrospective Trade Review reuses only v13 fast lineup reoptimization mechanics; v13 does not supply the trade judgment.",
}

VERSIONED_RE = re.compile(r"^(?P<prefix>.+)_v(?P<version>\d+)\.py$")
LOCAL_SCRIPT_RE = re.compile(r"(?:script/)?([A-Za-z0-9_]+(?:_v\d+)?\.py)")


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
    """Return local script references that actually exist in script/."""
    src = txt(path)
    refs = set()
    for name in LOCAL_SCRIPT_RE.findall(src):
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
    # Workflows are production roots only when their filename does not declare
    # them to be an audit/test/governance/validation exercise.
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
        pin_reason = INTENTIONAL_PINS.get(key)
        row = {
            "caller": rel(caller),
            "dependency": rel(dep),
            "family": family,
            "used_version": used,
            "latest_version": newest["version"],
            "latest_path": newest["path"],
            "intentional_pin": bool(pin_reason),
            "pin_reason": pin_reason,
        }
        stale.append(row)
        findings.append({
            "id": f"VERSION-FRESHNESS-{len(findings)+1:03d}",
            "ok": bool(pin_reason),
            "severity": "INFO" if pin_reason else "CRITICAL",
            "observation": (
                "Older version dependency is explicitly pinned as an internal/mechanical compatibility dependency."
                if pin_reason else
                "Production-reachable caller references an older version even though a newer module in the same family exists."
            ),
            **row,
        })

    # Also ensure every declared pin still corresponds to a real reachable edge.
    edge_keys = {(rel(a), rel(b)) for a, b in edges}
    dead_pins = []
    for key, reason in INTENTIONAL_PINS.items():
        if key not in edge_keys:
            dead_pins.append({"caller": key[0], "dependency": key[1], "reason": reason})

    failed = [x for x in findings if not x["ok"]]
    payload = {
        "model_version": MODEL_VERSION,
        "policy": {
            "new_version_requires_production_callers_to_advance": True,
            "silent_stale_dependencies_allowed": False,
            "intentional_older_dependency_requires_explicit_pin_and_reason": True,
            "production_reachability_scanned_recursively": True,
        },
        "production_roots": [rel(x) for x in roots],
        "reachable_file_count": len(reachable),
        "versioned_families": latest,
        "stale_dependency_references": stale,
        "dead_intentional_pins": dead_pins,
        "summary": {
            "passed": not failed,
            "stale_reference_count": len(stale),
            "unapproved_stale_reference_count": len(failed),
            "intentional_pin_count": sum(1 for x in stale if x["intentional_pin"]),
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
            "Unapproved stale production dependencies: "
            + ", ".join(f"{x['caller']} -> {x['dependency']}" for x in failed)
        )


if __name__ == "__main__":
    main()
