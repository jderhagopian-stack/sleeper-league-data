# FSFFL 2026 Preseason Media Guide

This directory documents the reproducible league-level preseason publication owned by the FSFFL Reporting layer.

## Production renderer

```bash
python script/league_intelligence/application.py \
  --focus-user-id 846634401482792960 \
  --team-context data/league_intelligence/hurts_so_good_team_context.json \
  --output /tmp/preseason-league-intelligence.json

python script/render_preseason_media_guide.py \
  --league-intelligence /tmp/preseason-league-intelligence.json \
  --output /tmp/FSFFL_2026_Preseason_Media_Guide.pdf
```

The standardized report-pack workflow runs the same sequence automatically and publishes the PDF as:

`preseason/2026-preseason-media-guide.pdf`

## Report contract

The guide is presentation-only. It consumes:

- governed Season Simulator outcomes;
- read-only League Intelligence positional percentiles;
- current Sleeper roster, taxi, and reserve state;
- current player age/team metadata; and
- the supported 2026 preseason position-specific projection bridge.

Every team receives a profile page and a complete roster/projection page.

The report may construct a projection-optimized legal lineup solely to organize the roster page. That display transform does not create a new team score, projection authority, valuation, recommendation, trade price, or acceptance probability.

Players without a supported preseason projection remain explicitly unavailable rather than receiving a report-only estimate.
