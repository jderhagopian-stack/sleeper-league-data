# Native projection core-context experiment

This branch tests the remaining first-order candidate feature families before freezing the V1 core: position-specific age/career stage, availability/durability shape, and position-specific lagged team context.

The experiment uses rolling 2021-2025 temporal holdouts and the accepted lag-1/lag-2 player-history model as its comparison point. Each family is tested separately and in a combined challenger. No target-season realized context or fantasy scoring is used as a training target.

The retention rule is deliberately strict: keep only position/family combinations that provide material and reasonably stable held-out improvement. Blanket all-position additions are not accepted merely because the variable is intuitively important. After these tests, further feature expansion is deferred unless residual-error analysis identifies a material gap.
