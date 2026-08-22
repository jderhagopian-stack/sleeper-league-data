# Replace the existing exact-version restore block in script/build_fsffl_gm30.py
# with the block below.

    # Restore the authoritative GM 3.0 football-intelligence contract.
    #
    # Validate the DATA CONTRACT, not an exact model-version string. This lets
    # future intelligence builders evolve without the inherited GM 2.2 core
    # accidentally overwriting them.
    required_intelligence_fields = {
        "active_season",
        "season_phase",
        "phase_weights",
        "prior_snaps",
        "preseason_usage",
    }
    if (
        isinstance(phase_aware_intelligence, dict)
        and required_intelligence_fields.issubset(phase_aware_intelligence.keys())
    ):
        dump(intelligence_path, phase_aware_intelligence)
