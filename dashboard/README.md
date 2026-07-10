# Dashboard

Two independent, unrelated implementations of the same "war room" view over
this suite's output. Neither is built/bundled by anything in this repo.

- **`CostPlusDashboard.html`** -- a static, standalone snapshot. Just open it
  in a browser; no server, no build step. It has a frozen, real top-25 rows
  and total baked in as of the date in the file, not a live fetch -- it will
  not reflect a newer run of `python run.py`.
- **`CostPlusDashboard.jsx`** -- a live React component (uses `papaparse`) that
  fetches `costplus_suite/output/leaderboard.csv`,
  `costplus_suite/output/spread_changes.csv`, and
  `costplus_suite/output/trumprx_comparison.csv` at runtime and re-renders
  whenever those files change. This repo has no `package.json`/bundler --
  drop it into an existing React app (served from `dashboard/`, per the path
  assumptions in the file's own header comment) to use it.

Both expect `python run.py` to have already been run from the repo root, so
`costplus_suite/output/*.csv` exist (they're gitignored, not committed).
