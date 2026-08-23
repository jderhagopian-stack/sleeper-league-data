# sleeper-league-data

## Alternate History Simulator

Experimental, isolated development currently lives on `feature/alternate-history-0.1` / PR #10.

Key design rules:
- completed NFL outcomes are immutable historical facts;
- only fantasy ownership, lineups, standings, draft capital and downstream decisions may change;
- the core engine is league-agnostic, with FSFFL implemented through an adapter;
- Simulator 1.0 and GM 3.0 remain read-only dependencies;
- generated alternate-history artifacts are restricted to `data/alternate_history/`.

Validated stages on the feature branch:
- 0.1 historical ownership/fork foundation;
- 0.2 no-fork replay against the independently-built Record Book;
- 0.3 direct historical counterfactual replay with a no-hindsight lineup policy.

Initial validation scenario: Puka Nacua vs. Van Jefferson, September 2023.
