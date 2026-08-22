#!/usr/bin/env python3
"""FSFFL GM 3.0 — dedicated prospect intelligence engine."""
from __future__ import annotations
import json, math
from pathlib import Path

DATA = Path('data')
CONFIG = DATA / 'gm3_prospect_config.json'
INPUTS = DATA / 'gm3_prospect_inputs.json'
OUT = DATA / 'gm'
OUTPUT = OUT / 'gm30_prospect_radar.json'


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
    # Inputs should normally be 0..1. These transforms support raw contract fields.
    if field == 'draft_capital':
        # Accept either a normalized score or an overall NFL draft pick.
        return clamp(v) if 0 <= v <= 1 else clamp(1.0 - (v - 1.0) / 256.0)
    if field in ('age', 'breakout_age'):
        # Younger is generally better; deliberately broad rather than pretending precision.
        return clamp((24.0 - v) / 5.0)
    if field.endswith('_inverse'):
        # If supplied raw, convert a percentage/rate where lower is better.
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


def classify(row, thresholds):
    tags = []
    score = row['prospect_score']
    if score is None: return tags
    if score >= thresholds.get('BLUE_CHIP', 82): tags.append('BLUE_CHIP')
    mr, model_rank = row.get('market_rookie_rank'), row.get('model_rookie_rank')
    if score >= thresholds.get('DRAFT_SLEEPER', 70) and mr and model_rank and mr - model_rank >= 4:
        tags.append('DRAFT_SLEEPER')
    # Bust risk requires an expensive market price plus materially weaker model profile.
    if mr and model_rank and mr <= 18 and model_rank - mr >= 5 and score <= 100 - thresholds.get('BUST_RISK', 72) / 2:
        tags.append('BUST_RISK')
    return tags


def main():
    cfg = load(CONFIG, {})
    payload = load(INPUTS, {'prospects': []})
    prospects = payload.get('prospects', []) if isinstance(payload, dict) else []
    weights_by_pos = cfg.get('weights', {})
    rows = []
    for p in prospects:
        if not isinstance(p, dict): continue
        pos = str(p.get('position', '')).upper()
        weights = weights_by_pos.get(pos)
        if not weights: continue
        score, coverage, components = weighted_score(p, weights)
        rows.append({
            'player_id': p.get('player_id') or p.get('sleeper_id'),
            'name': p.get('name'), 'position': pos, 'class': p.get('class'),
            'prospect_score': score, 'feature_coverage': coverage,
            'market_rookie_rank': market_rank(p), 'components': components,
        })

    # Rank within position so QB/RB/WR/TE are not distorted by different feature sets.
    by_pos = {}
    for row in rows: by_pos.setdefault(row['position'], []).append(row)
    for group in by_pos.values():
        group.sort(key=lambda r: (-1 if r['prospect_score'] is None else r['prospect_score']), reverse=True)
        for i, row in enumerate(group, 1): row['model_position_rank'] = i

    # Overall rookie rank is useful for market disagreement; score is the transparent prior.
    ranked = sorted(rows, key=lambda r: (-1 if r['prospect_score'] is None else r['prospect_score']), reverse=True)
    for i, row in enumerate(ranked, 1): row['model_rookie_rank'] = i
    thresholds = cfg.get('thresholds', {})
    for row in ranked: row['signals'] = classify(row, thresholds)

    OUT.mkdir(parents=True, exist_ok=True)
    output = {
        'model_version': cfg.get('model_version', 'FSFFL-GM-3.0-Prospect-Engine-v1'),
        'prospect_count': len(ranked),
        'note': 'Scores are transparent priors. Missing features reduce coverage rather than count as negative evidence.',
        'prospects': ranked,
    }
    with OUTPUT.open('w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f'GM 3.0 prospect engine: {len(ranked)} prospects -> {OUTPUT}')


if __name__ == '__main__':
    main()
