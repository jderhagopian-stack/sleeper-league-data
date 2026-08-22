#!/usr/bin/env python3
"""FSFFL GM 3.0 — dedicated prospect intelligence engine v1.2."""
from __future__ import annotations
import json, math
from pathlib import Path

DATA = Path('data')
CONFIG = DATA / 'gm3_prospect_config.json'
INPUTS = DATA / 'gm3_prospect_inputs.json'
OUT = DATA / 'gm'
OUTPUT = OUT / 'gm30_prospect_radar.json'

# Prevent sparse automatic rows from receiving strong scouting labels.
DEFAULT_SIGNAL_MIN_COVERAGE = 0.55
DEFAULT_RANK_MIN_COVERAGE = 0.20


def load(path, default):
    try:
        with Path(path).open('r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def number(x):
    try:
        if x is None or isinstance(x, bool): return None
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def normalize_metric(field, raw):
    v = number(raw)
    if v is None: return None
    if field == 'draft_capital':
        # Accept normalized score or explicit overall NFL draft pick.
        return clamp(v) if 0 <= v <= 1 else clamp(1.0 - (v - 1.0) / 256.0)
    if field in ('age', 'breakout_age'):
        return clamp((24.0 - v) / 5.0)
    if field.endswith('_inverse'):
        if 0 <= v <= 1: return clamp(1.0 - v)
        if 0 <= v <= 100: return clamp(1.0 - v / 100.0)
    return clamp(v)


def weighted_score(prospect, weights):
    used, possible, total = 0.0, sum(weights.values()), 0.0
    components = {}
    for field, weight in weights.items():
        raw = prospect.get(field)
        if raw is None and field == 'draft_capital':
            raw = prospect.get('draft_capital_pick')
        val = normalize_metric(field, raw)
        if val is None: continue
        used += weight
        total += val * weight
        components[field] = round(val * 100, 1)
    if used == 0:
        return None, 0.0, components
    return round(100 * total / used, 1), round(used / possible, 3), components


def market_rank(prospect):
    v = number(prospect.get('market_rookie_rank'))
    return int(v) if v is not None and v > 0 else None


def classify(row, thresholds, signal_min_coverage):
    tags = []
    score = row['prospect_score']
    if score is None:
        return tags

    # Critical sparse-data gate.
    if row.get('feature_coverage', 0.0) < signal_min_coverage:
        return tags

    if score >= thresholds.get('BLUE_CHIP', 82):
        tags.append('BLUE_CHIP')

    mr, model_rank = row.get('market_rookie_rank'), row.get('model_rookie_rank')
    if (
        score >= thresholds.get('DRAFT_SLEEPER', 70)
        and mr and model_rank and mr - model_rank >= 4
    ):
        tags.append('DRAFT_SLEEPER')

    if (
        mr and model_rank and mr <= 18 and model_rank - mr >= 5
        and score <= 100 - thresholds.get('BUST_RISK', 72) / 2
    ):
        tags.append('BUST_RISK')

    return tags


def main():
    cfg = load(CONFIG, {})
    payload = load(INPUTS, {'prospects': []})
    prospects = payload.get('prospects', []) if isinstance(payload, dict) else []
    weights_by_pos = cfg.get('weights', {})

    signal_min_coverage = float(
        cfg.get('signal_min_feature_coverage', DEFAULT_SIGNAL_MIN_COVERAGE)
    )
    rank_min_coverage = float(
        cfg.get('rank_min_feature_coverage', DEFAULT_RANK_MIN_COVERAGE)
    )

    rows = []
    for p in prospects:
        if not isinstance(p, dict): continue
        pos = str(p.get('position', '')).upper()
        weights = weights_by_pos.get(pos)
        if not weights: continue

        score, coverage, components = weighted_score(p, weights)
        rows.append({
            'player_id': p.get('player_id') or p.get('sleeper_id'),
            'name': p.get('name'),
            'position': pos,
            'class': p.get('class'),
            'prospect_stage': p.get('prospect_stage'),
            'nfl_team': p.get('nfl_team'),
            'prospect_score': score,
            'feature_coverage': coverage,
            'signal_eligible': bool(
                score is not None and coverage >= signal_min_coverage
            ),
            'rank_eligible': bool(
                score is not None and coverage >= rank_min_coverage
            ),
            'market_rookie_rank': market_rank(p),
            'market_dynasty': p.get('market_dynasty'),
            'draft_capital_pick': p.get('draft_capital_pick'),
            'components': components,
        })

    # Position rank only among rows with enough evidence to support a model rank.
    by_pos = {}
    for row in rows:
        if row['rank_eligible']:
            by_pos.setdefault(row['position'], []).append(row)
    for group in by_pos.values():
        group.sort(
            key=lambda r: (-1 if r['prospect_score'] is None else r['prospect_score']),
            reverse=True
        )
        for i, row in enumerate(group, 1):
            row['model_position_rank'] = i

    # Overall model rank likewise requires minimum evidence.
    ranked_eligible = sorted(
        [r for r in rows if r['rank_eligible']],
        key=lambda r: (-1 if r['prospect_score'] is None else r['prospect_score']),
        reverse=True
    )
    for i, row in enumerate(ranked_eligible, 1):
        row['model_rookie_rank'] = i

    for row in rows:
        row.setdefault('model_position_rank', None)
        row.setdefault('model_rookie_rank', None)

    thresholds = cfg.get('thresholds', {})
    for row in rows:
        row['signals'] = classify(row, thresholds, signal_min_coverage)

    # Display order: signal-eligible/high-coverage first, then sparse priors by
    # market rookie rank so current rookies remain visible without fake certainty.
    rows.sort(
        key=lambda r: (
            bool(r.get('signal_eligible')),
            float(r.get('feature_coverage') or 0.0),
            -int(r.get('market_rookie_rank') or 9999),
            float(r.get('prospect_score') or -1.0),
        ),
        reverse=True,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    output = {
        'model_version': cfg.get('model_version', 'FSFFL-GM-3.0-Prospect-Engine-v1.2'),
        'prospect_count': len(rows),
        'signal_eligible_count': sum(1 for r in rows if r['signal_eligible']),
        'sparse_prior_count': sum(1 for r in rows if not r['signal_eligible']),
        'signal_min_feature_coverage': signal_min_coverage,
        'rank_min_feature_coverage': rank_min_coverage,
        'note': (
            'Current rookies remain visible as prospect priors. Missing features '
            'reduce coverage. Strong BLUE_CHIP/DRAFT_SLEEPER/BUST_RISK labels are '
            'forbidden below the signal coverage threshold.'
        ),
        'prospects': rows,
    }
    with OUTPUT.open('w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"GM 3.0 prospect engine: {len(rows)} prospects; "
        f"{output['signal_eligible_count']} signal-eligible; "
        f"{output['sparse_prior_count']} sparse priors -> {OUTPUT}"
    )


if __name__ == '__main__':
    main()
