#!/usr/bin/env python3
"""Point-in-time historical FSFFL rookie-pick coordinate.

Research/calibration infrastructure only.

Hard invariants
---------------
* 2022 startup draft nomination/order mechanics are never rookie-pick evidence.
* A trade at timestamp T can use only draft evidence completed before T.
* Eventual pick slot / selected-player outcomes are not inputs before they were known.
* Current production pick values and current external market tables are not inputs.
* Historical team strength informs only unresolved slot distribution, not a second
  independent value premium.
* Horizon discount magnitude is not claimed as empirically identified. It is exposed
  as a bounded research sensitivity rail.

The provider intentionally produces a distribution/rail when the slot is unresolved.
It does not force false precision.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
DATA = ROOT / "data"

MODEL_VERSION = "FSFFL-Historical-Pick-Coordinate-1.0"
LEAGUE_SIZE = 12
ROOKIE_ROUNDS = 3

# These are explicit research sensitivity rails, not production coefficients.
# They represent "no time discount" through a deliberately broad lower scenario.
# Promotion/calibration of a time curve requires separate evidence.
HORIZON_ANNUAL_SENSITIVITY = {
    "lower": 0.80,
    "center": 0.90,
    "upper": 1.00,
}
HORIZON_STATUS = "BOUNDED_RESEARCH_SENSITIVITY_NOT_EMPIRICALLY_IDENTIFIED"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


STATE_MOD = _load_module(SCRIPT / "fsffl_historical_state_provider.py", "historical_pick_state")
BEHAVIOR_MOD = _load_module(SCRIPT / "historical_state_behavior.py", "historical_pick_behavior")
BUNDLE_MOD = _load_module(SCRIPT / "build_historical_gm3_bundle.py", "historical_pick_bundle")
GM_MOD = _load_module(SCRIPT / "build_fsffl_gm_engine.py", "historical_pick_gm")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _sf(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _si(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _quantile(values: Sequence[float], q: float) -> float | None:
    vals = sorted(float(x) for x in values)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = max(0.0, min(1.0, q)) * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def _utc_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000.0, tz=timezone.utc).isoformat()


def _pick_key(season: int, rnd: int, original_roster_id: int | str) -> str:
    return f"pick:{int(season)}:R{int(rnd)}:orig{int(original_roster_id)}"


def _draft_entry(data: Mapping[str, Any], draft_id: str) -> Mapping[str, Any] | None:
    for entry in data.get("drafts") or []:
        d = entry.get("draft") or {}
        if str(d.get("draft_id") or "") == str(draft_id):
            return entry
    return None


def _draft_start_ms(entry: Mapping[str, Any] | None) -> int:
    d = (entry or {}).get("draft") or {}
    return _si(d.get("start_time") or d.get("created"), 0)


def _draft_complete_ms(entry: Mapping[str, Any] | None) -> tuple[int, str]:
    """Return the earliest defensible evidence-availability timestamp.

    Prefer actual per-pick timestamps. If those are absent, use a conservative
    seven-day post-start boundary rather than pretending selections were known
    at draft start.
    """
    if not entry:
        return 0, "MISSING_DRAFT_ENTRY"
    pick_times = []
    for p in entry.get("picks") or []:
        for key in ("picked_at", "created", "timestamp"):
            ts = _si(p.get(key), 0)
            if ts > 0:
                pick_times.append(ts)
                break
    if pick_times:
        return max(pick_times), "MAX_RECORDED_PICK_TIMESTAMP"
    start = _draft_start_ms(entry)
    if start > 0:
        return start + 7 * 24 * 60 * 60 * 1000, "CONSERVATIVE_SEVEN_DAY_POST_START_FALLBACK"
    return 0, "NO_RELIABLE_COMPLETION_TIMESTAMP"


def _tier_for_slot(slot: int) -> str:
    slot = max(1, min(LEAGUE_SIZE, int(slot)))
    if slot <= 4:
        return "early"
    if slot <= 8:
        return "mid"
    return "late"


def _slot_distribution_from_strength(
    strength_score: float,
    confidence: float,
    horizon_seasons: int,
) -> Dict[int, float]:
    """Map point-in-time team strength to a 12-slot probability distribution.

    The expected draft slot is a direct league-size transform of strength percentile:
    weakest=1.01, strongest=1.12. Confidence controls a convex blend between a
    local slot kernel and an uninformative uniform distribution.

    Future-year uncertainty is widened transparently by reducing confidence in
    proportion to horizon. This is a research uncertainty transform, not a value bonus.
    """
    strength = max(0.0, min(1.0, float(strength_score)))
    horizon = max(1, int(horizon_seasons))
    effective_conf = max(0.0, min(1.0, float(confidence))) / float(horizon)
    expected = 1.0 + (LEAGUE_SIZE - 1) * strength

    # Local triangular kernel centered on the structurally-derived expected slot.
    local = {}
    for slot in range(1, LEAGUE_SIZE + 1):
        local[slot] = max(0.0, 1.0 - abs(slot - expected) / 4.0)
    denom = sum(local.values()) or 1.0
    local = {slot: weight / denom for slot, weight in local.items()}

    uniform = 1.0 / LEAGUE_SIZE
    probs = {
        slot: effective_conf * local[slot] + (1.0 - effective_conf) * uniform
        for slot in range(1, LEAGUE_SIZE + 1)
    }
    total = sum(probs.values()) or 1.0
    return {slot: prob / total for slot, prob in probs.items()}


def _team_strength_as_of(
    trade_season: int,
    trade_timestamp_ms: int,
    original_roster_id: int | str,
) -> Dict[str, Any]:
    """Reuse the existing historical standings reconstruction without future leakage."""
    created_utc = _utc_iso(trade_timestamp_ms)
    rid = str(original_roster_id)
    through_week = int(BEHAVIOR_MOD.completed_week_before_trade(int(trade_season), created_utc))

    if through_week > 0:
        perf = BEHAVIOR_MOD.performance_signal(int(trade_season), rid, through_week)
        if perf:
            score = 0.64 * _sf(perf.get("record_percentile"), 0.5) + 0.36 * _sf(
                perf.get("points_percentile"), 0.5
            )
            confidence = max(0.35, min(0.92, 0.40 + 0.04 * through_week))
            return {
                "strength_score": round(score, 6),
                "confidence": round(confidence, 6),
                "source": "CURRENT_SEASON_RESULTS_THROUGH_COMPLETED_WEEK",
                "performance_source_season": int(trade_season),
                "performance_through_week": through_week,
                "uses_future_same_season_results": False,
            }

    prior = int(trade_season) - 1
    perf = BEHAVIOR_MOD.performance_signal(prior, rid, BEHAVIOR_MOD.REGULAR_SEASON_WEEKS) if prior >= 2022 else None
    if perf:
        score = 0.67 * _sf(perf.get("record_percentile"), 0.5) + 0.33 * _sf(
            perf.get("points_percentile"), 0.5
        )
        return {
            "strength_score": round(score, 6),
            "confidence": 0.62,
            "source": "PRIOR_COMPLETED_SEASON_RESULTS",
            "performance_source_season": prior,
            "performance_through_week": int(BEHAVIOR_MOD.REGULAR_SEASON_WEEKS),
            "uses_future_same_season_results": False,
        }

    return {
        "strength_score": 0.5,
        "confidence": 0.0,
        "source": "NO_DEFENSIBLE_PERFORMANCE_ANCHOR",
        "performance_source_season": None,
        "performance_through_week": 0,
        "uses_future_same_season_results": False,
    }


@dataclass(frozen=True)
class DraftEvidence:
    season: int
    draft_id: str
    start_ms: int
    complete_ms: int
    completion_basis: str
    slot_player_ids: Dict[int, str]
    slot_values: Dict[int, float]
    value_basis: str


class HistoricalPickCoordinateProvider:
    """Build leakage-safe historical pick coordinates from prior FSFFL evidence."""

    def __init__(
        self,
        *,
        history_provider=None,
        conversion_index: Sequence[Mapping[str, Any]] | None = None,
    ):
        self.history_provider = history_provider or STATE_MOD.HistoricalStateProvider()
        self.conversion_index = list(
            conversion_index
            if conversion_index is not None
            else (_load_json(DATA / "draft_pick_conversion_index.json", []) or [])
        )
        self._draft_evidence_cache: Dict[int, DraftEvidence | None] = {}
        self._players = BUNDLE_MOD.player_index()

    def draft_rows(self, season: int) -> List[Mapping[str, Any]]:
        if int(season) == 2022:
            return []
        return [r for r in self.conversion_index if _si(r.get("season"), 0) == int(season)]

    def draft_evidence(self, season: int) -> DraftEvidence | None:
        season = int(season)
        if season in self._draft_evidence_cache:
            return self._draft_evidence_cache[season]
        if season <= 2022:
            self._draft_evidence_cache[season] = None
            return None

        rows = self.draft_rows(season)
        if not rows:
            self._draft_evidence_cache[season] = None
            return None
        draft_ids = sorted({str(r.get("draft_id") or "") for r in rows if r.get("draft_id")})
        if len(draft_ids) != 1:
            self._draft_evidence_cache[season] = None
            return None
        draft_id = draft_ids[0]

        try:
            data = self.history_provider.data(str(season))
        except Exception:
            self._draft_evidence_cache[season] = None
            return None
        entry = _draft_entry(data, draft_id)
        start_ms = _draft_start_ms(entry)
        complete_ms, completion_basis = _draft_complete_ms(entry)
        if complete_ms <= 0:
            self._draft_evidence_cache[season] = None
            return None

        # Build a synthetic draft-class roster so the existing historical intrinsic
        # player-value method can evaluate every drafted player from information
        # available around that draft. No current market table is passed.
        pids = [str(r.get("player_id") or "") for r in rows if r.get("player_id")]
        synthetic_rosters = [{
            "roster_id": 999,
            "owner_id": "historical-pick-coordinate",
            "players": sorted(set(pids)),
            "taxi": [],
            "reserve": [],
        }]
        prior, baselines, _ = BUNDLE_MOD.scoring_as_of(
            int(season), max(start_ms, 1), self._players
        )
        values, external_exact_count = BUNDLE_MOD.build_player_values(
            synthetic_rosters,
            self._players,
            prior,
            baselines,
            {},
            int(season),
        )
        if external_exact_count != 0:
            raise AssertionError("Historical pick coordinate unexpectedly used an external exact player source")

        slot_player_ids: Dict[int, str] = {}
        slot_values: Dict[int, float] = {}
        for r in rows:
            rnd = _si(r.get("round"), 0)
            slot_in_round = _si(r.get("draft_slot"), 0)
            pid = str(r.get("player_id") or "")
            if rnd not in (1, 2, 3) or not (1 <= slot_in_round <= LEAGUE_SIZE) or not pid:
                continue
            overall_slot = (rnd - 1) * LEAGUE_SIZE + slot_in_round
            asset = values.get(pid)
            if not asset:
                continue
            slot_player_ids[overall_slot] = pid
            slot_values[overall_slot] = float(GM_MOD.fsffl_league_value(asset))

        evidence = DraftEvidence(
            season=season,
            draft_id=draft_id,
            start_ms=start_ms,
            complete_ms=complete_ms,
            completion_basis=completion_basis,
            slot_player_ids=slot_player_ids,
            slot_values=slot_values,
            value_basis="FSFFL_RECONSTRUCTED_AT_DRAFT_INTRINSIC_NO_EXTERNAL_EXACT_SOURCE",
        )
        self._draft_evidence_cache[season] = evidence
        return evidence

    def available_evidence(self, trade_timestamp_ms: int) -> List[DraftEvidence]:
        """Only completed prior rookie drafts may teach the coordinate at T."""
        out = []
        for season in sorted({_si(r.get("season"), 0) for r in self.conversion_index}):
            if season <= 2022:
                continue
            ev = self.draft_evidence(season)
            if ev is not None and ev.complete_ms > 0 and ev.complete_ms <= int(trade_timestamp_ms):
                out.append(ev)
        return out

    def exact_slot_known(
        self,
        *,
        trade_timestamp_ms: int,
        pick_season: int,
        rnd: int,
        original_roster_id: int | str,
    ) -> Dict[str, Any]:
        """Use final slot only once the historical draft has actually begun.

        This is intentionally conservative. If the repo later gains an explicit
        draft-order lock timestamp, that can safely move the boundary earlier.
        """
        if int(pick_season) <= 2022:
            return {"known": False, "reason": "STARTUP_NOT_ROOKIE_PICK_EVIDENCE"}
        rows = [
            r for r in self.draft_rows(int(pick_season))
            if _si(r.get("round"), 0) == int(rnd)
            and _si(r.get("original_roster_id"), -1) == int(original_roster_id)
        ]
        if len(rows) != 1:
            return {"known": False, "reason": "NO_UNIQUE_HISTORICAL_SLOT_MAPPING"}
        ev = self.draft_evidence(int(pick_season))
        if ev is None or ev.start_ms <= 0:
            return {"known": False, "reason": "NO_RELIABLE_DRAFT_START_TIMESTAMP"}
        if int(trade_timestamp_ms) < ev.start_ms:
            return {"known": False, "reason": "DRAFT_NOT_YET_STARTED_AT_TRADE_TIME"}
        slot = _si(rows[0].get("draft_slot"), 0)
        if not 1 <= slot <= LEAGUE_SIZE:
            return {"known": False, "reason": "INVALID_SLOT_MAPPING"}
        return {
            "known": True,
            "slot_in_round": slot,
            "reason": "DRAFT_STARTED_SLOT_MAPPING_HISTORICALLY_KNOWABLE",
            "draft_start_ms": ev.start_ms,
        }

    @staticmethod
    def _slot_samples(
        evidence: Sequence[DraftEvidence],
        rnd: int,
        slot_in_round: int,
    ) -> List[float]:
        overall = (int(rnd) - 1) * LEAGUE_SIZE + int(slot_in_round)
        return [
            float(ev.slot_values[overall])
            for ev in evidence
            if overall in ev.slot_values
        ]

    def slot_curve(
        self,
        *,
        trade_timestamp_ms: int,
        rnd: int,
    ) -> Dict[int, Dict[str, Any]]:
        evidence = self.available_evidence(int(trade_timestamp_ms))
        out: Dict[int, Dict[str, Any]] = {}
        round_pool = []
        for slot in range(1, LEAGUE_SIZE + 1):
            round_pool.extend(self._slot_samples(evidence, rnd, slot))

        round_median = statistics.median(round_pool) if round_pool else None
        for slot in range(1, LEAGUE_SIZE + 1):
            samples = self._slot_samples(evidence, rnd, slot)
            if samples:
                center = statistics.median(samples)
                lo = _quantile(samples, 0.25)
                hi = _quantile(samples, 0.75)
                basis = "EMPIRICAL_PRIOR_COMPLETED_FSFFL_DRAFT_SLOT"
            elif round_median is not None:
                # Sparse single-season slot holes retain the round-level center but
                # are explicitly lower quality; no present-day value is substituted.
                center = round_median
                lo = _quantile(round_pool, 0.20)
                hi = _quantile(round_pool, 0.80)
                basis = "SPARSE_PRIOR_DRAFT_ROUND_POOL"
            else:
                center = lo = hi = None
                basis = "NO_PRE_TRADE_FSFFL_ROOKIE_DRAFT_VALUE_EVIDENCE"
            out[slot] = {
                "center": center,
                "lower": lo,
                "upper": hi,
                "sample_count": len(samples),
                "basis": basis,
            }
        return out

    def historical_pick_value(
        self,
        *,
        trade_timestamp_ms: int,
        trade_season: int,
        pick_season: int,
        rnd: int,
        original_roster_id: int | str,
    ) -> Dict[str, Any]:
        trade_timestamp_ms = int(trade_timestamp_ms)
        trade_season = int(trade_season)
        pick_season = int(pick_season)
        rnd = int(rnd)
        original_roster_id = int(original_roster_id)
        key = _pick_key(pick_season, rnd, original_roster_id)

        if trade_season <= 2022 or pick_season <= 2022:
            return {
                "asset_key": key,
                "status": "EXCLUDED",
                "evidence_quality": "EXCLUDED",
                "calibration_suitability": "EXCLUDED",
                "reason": "2022_STARTUP_NOMINATION_MECHANICS_NOT_ROOKIE_PICK_VALUE_EVIDENCE",
                "production_authority": False,
                "no_leakage": True,
            }
        if rnd not in (1, 2, 3):
            return {
                "asset_key": key,
                "status": "EXCLUDED",
                "evidence_quality": "EXCLUDED",
                "calibration_suitability": "EXCLUDED",
                "reason": "OUTSIDE_FSFFL_THREE_ROUND_ROOKIE_DRAFT",
                "production_authority": False,
                "no_leakage": True,
            }

        prior_evidence = self.available_evidence(trade_timestamp_ms)
        curve = self.slot_curve(trade_timestamp_ms=trade_timestamp_ms, rnd=rnd)
        exact = self.exact_slot_known(
            trade_timestamp_ms=trade_timestamp_ms,
            pick_season=pick_season,
            rnd=rnd,
            original_roster_id=original_roster_id,
        )

        horizon = max(0, pick_season - trade_season)
        exact_slot = bool(exact.get("known"))
        if exact_slot:
            slot_probs = {slot: 1.0 if slot == int(exact["slot_in_round"]) else 0.0 for slot in range(1, LEAGUE_SIZE + 1)}
            strength = {
                "strength_score": None,
                "confidence": 1.0,
                "source": "EXACT_SLOT_KNOWN_TEAM_STRENGTH_NOT_USED_FOR_SLOT",
                "uses_future_same_season_results": False,
            }
        else:
            strength = _team_strength_as_of(trade_season, trade_timestamp_ms, original_roster_id)
            slot_probs = _slot_distribution_from_strength(
                _sf(strength.get("strength_score"), 0.5),
                _sf(strength.get("confidence"), 0.0),
                max(1, horizon + (0 if pick_season > trade_season else 1)),
            )

        centers = []
        lows = []
        highs = []
        missing_mass = 0.0
        for slot, prob in slot_probs.items():
            cell = curve.get(slot) or {}
            if cell.get("center") is None:
                missing_mass += prob
                continue
            centers.append(prob * float(cell["center"]))
            lows.append(prob * float(cell.get("lower") if cell.get("lower") is not None else cell["center"]))
            highs.append(prob * float(cell.get("upper") if cell.get("upper") is not None else cell["center"]))

        base_center = sum(centers) if centers else None
        base_lower = sum(lows) if lows else None
        base_upper = sum(highs) if highs else None

        if base_center is None or missing_mass > 0.50:
            evidence_quality = "LOW"
            suitability = "SENSITIVITY_ONLY"
            status = "INSUFFICIENT_PRE_TRADE_DRAFT_VALUE_EVIDENCE"
            center = lower = upper = None
        else:
            years_to_realization = max(0, pick_season - trade_season)
            lower_factor = HORIZON_ANNUAL_SENSITIVITY["lower"] ** years_to_realization
            center_factor = HORIZON_ANNUAL_SENSITIVITY["center"] ** years_to_realization
            upper_factor = HORIZON_ANNUAL_SENSITIVITY["upper"] ** years_to_realization
            center = base_center * center_factor
            lower = min(base_lower if base_lower is not None else base_center, base_center) * lower_factor
            upper = max(base_upper if base_upper is not None else base_center, base_center) * upper_factor

            draft_count = len(prior_evidence)
            if exact_slot and draft_count >= 2:
                evidence_quality = "HIGH"
                suitability = "DIRECT_CALIBRATION"
            elif draft_count >= 2:
                evidence_quality = "MEDIUM"
                suitability = "LOWER_WEIGHT_CALIBRATION"
            elif draft_count == 1:
                # One completed pre-trade FSFFL rookie draft is sparse, but it is
                # still real point-in-time league-owned evidence. Preserve it at
                # LOW quality / lower weight rather than converting uncertainty
                # into zero calibration authority. The explicit value rails and
                # trade-level evidence weight carry the uncertainty forward.
                evidence_quality = "LOW"
                suitability = "LOWER_WEIGHT_CALIBRATION"
            else:
                evidence_quality = "LOW"
                suitability = "SENSITIVITY_ONLY"
            status = "RECONSTRUCTED"

        expected_slot = sum(slot * prob for slot, prob in slot_probs.items())
        tier_probs = {
            tier: round(sum(prob for slot, prob in slot_probs.items() if _tier_for_slot(slot) == tier), 6)
            for tier in ("early", "mid", "late")
        }

        evidence_seasons = [ev.season for ev in prior_evidence]
        provenance = {
            "model_version": MODEL_VERSION,
            "source_hierarchy": [
                "FSFFL_OWNED_ROOKIE_DRAFT_HISTORY",
                "FSFFL_HISTORICAL_STATE_RECONSTRUCTION",
                "FSFFL_RECONSTRUCTED_AT_DRAFT_INTRINSIC_PLAYER_COORDINATE",
                "BOUNDED_RESEARCH_HORIZON_SENSITIVITY",
            ],
            "draft_evidence_seasons_available_before_trade": evidence_seasons,
            "draft_evidence_completion_basis": {
                str(ev.season): ev.completion_basis for ev in prior_evidence
            },
            "external_historical_market_source_used": False,
            "restricted_proprietary_source_used": False,
            "current_pick_values_used": False,
            "current_market_values_used": False,
            "eventual_selected_player_used_for_this_pick": False,
            "future_draft_results_used": False,
            "future_same_season_results_used_for_slot": False,
            "production_authority": False,
            "research_only": True,
        }

        return {
            "asset_key": key,
            "status": status,
            "trade_timestamp_ms": trade_timestamp_ms,
            "trade_timestamp_utc": _utc_iso(trade_timestamp_ms),
            "trade_season": trade_season,
            "pick_season": pick_season,
            "round": rnd,
            "original_roster_id": original_roster_id,
            "value_center": round(center, 4) if center is not None else None,
            "value_lower": round(lower, 4) if lower is not None else None,
            "value_upper": round(upper, 4) if upper is not None else None,
            "exact_slot_known_at_trade_time": exact_slot,
            "exact_slot": int(exact.get("slot_in_round")) if exact_slot else None,
            "exact_slot_basis": exact.get("reason"),
            "expected_slot_in_round": round(expected_slot, 4),
            "expected_tier": max(tier_probs, key=tier_probs.get),
            "tier_probabilities": tier_probs,
            "slot_distribution": {str(slot): round(prob, 8) for slot, prob in slot_probs.items()},
            "team_state_slot_evidence": strength,
            "time_to_realization_seasons": max(0, pick_season - trade_season),
            "horizon_sensitivity_status": HORIZON_STATUS,
            "horizon_annual_sensitivity": dict(HORIZON_ANNUAL_SENSITIVITY),
            "evidence_quality": evidence_quality,
            "calibration_suitability": suitability,
            "uncertainty_relative_width": (
                round((upper - lower) / max(abs(center), 1.0), 6)
                if center is not None and lower is not None and upper is not None else None
            ),
            "provenance": provenance,
        }


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--trade-timestamp-ms", type=int, required=True)
    ap.add_argument("--trade-season", type=int, required=True)
    ap.add_argument("--pick-season", type=int, required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--original-roster-id", type=int, required=True)
    args = ap.parse_args()

    provider = HistoricalPickCoordinateProvider()
    row = provider.historical_pick_value(
        trade_timestamp_ms=args.trade_timestamp_ms,
        trade_season=args.trade_season,
        pick_season=args.pick_season,
        rnd=args.round,
        original_roster_id=args.original_roster_id,
    )
    print(json.dumps(row, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
