# GM 3.0.0 Frozen Production Baseline

This directory freezes the validated GM 3.0.0 decision architecture as of source commit `5e235574d4ff41e8c8075dc02aeea20c3488c210`.

The freeze protects the model architecture, not live football data. Sleeper rosters, market values, injuries, usage, projections, prospects, simulator outputs, and other time-sensitive inputs should continue to refresh normally.

The `manifest.json` records the exact source commit, key output blob SHAs, critical model-component SHAs, validation status, and versioning policy. The `snapshot/` directory contains exact Git-level copies of the most important validated GM 3.0 outputs for convenient regression comparison.

Versioning policy: `3.0.x` is reserved for bug fixes that do not materially change the decision architecture; `3.x` is for calibrated model improvements; `4.0` is for a fundamental architecture change.

Do not overwrite this reference in place. Future baselines should receive a new versioned reference directory.
