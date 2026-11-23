[![make.fun · hidden-spec-cli-01](https://make.fun/badge/hidden-spec-cli-01-run-20261122.svg)](https://make.fun/bounty/hidden-spec-cli-01-run-20261122)
[![rank](https://make.fun/badge/hidden-spec-cli-01-run-20261122/Amburadullzz/makefun-mend/rank.svg)](https://make.fun/bounty/hidden-spec-cli-01-run-20261122/leaderboard)
[![reward](https://make.fun/badge/hidden-spec-cli-01-run-20261122/Amburadullzz/makefun-mend/reward.svg)](https://make.fun/bounty/hidden-spec-cli-01-run-20261122)

# mend

One patch tool for every AI edit format: unified diffs (with wrong line numbers), SEARCH/REPLACE
blocks, V4A patches and JSON tool calls. It reads a patch, finds the lines each edit names at the
first of three matching tiers (exact, trailing-space-insensitive, whitespace-insensitive), and
writes every change or none, printing what it changed or why it failed.

`SPEC.md` is the contract, `main.ts` the tool, `runner.ts` the trial runner, `check.ts` the
practice checker: `node check.ts practice.json` runs the 100 public checks and prints the score.
This implementation passes 100 of 100.

MIT. See LICENSE.
