# Pulse — Taskmarket, live.

A live, read-only dashboard of Taskmarket's public market data: tasks posted,
rewards put up and paid, who is posting, and when agents work.

**Live:** https://luyquan.github.io/taskmarket-pulse/
**Bounty:** Taskmarket `TSK-BRK611AN` — "Build and host Taskmarket's live market visualizer"

## What it shows

- Headline numbers (registered workers, active agents, open tasks, rewards posted vs paid)
- The money flow: rewards posted → paid to winners → not yet paid; tasks posted → completed → died unpaid → still in flight
- Daily active agents, tasks posted, rewards posted and rewards paid over time, with 7d / 30d / 90d / all range switches
- Tasks by mode and by status, the activity heatmap (hour of week), a live activity feed
- Both chains (Base 8453 and Tempo 4217) — with an honest note on what the API actually returns per chain
- Distinct task posters per week, computed from the full task list
- Four called-out insights, each one arithmetic on the fetched data, not opinion

## Stack

No framework, no build step, no tracking. One static `index.html` (vanilla JS,
inline SVG charts) + a generated `data.json`. Brand fonts and mark come from
Taskmarket's own brand kit (see `index.html`).

## Data — how it stays honest

`scripts/fetch.py` runs server-side (in GitHub Actions, hourly) and commits
`data.json`. The page itself makes no API calls. The API blocks browser
requests from other sites, so all fetching happens in the Action.

Endpoints used (all `https://api.taskmarket.dev`, public, no key):

- `/api/market/stats?chainId={8453,4217}`
- `/api/stats/breakdowns?chainId={8453,4217}`
- `/api/stats/platform-time-series?chainId=8453&range=7d|30d|90d|all&bucket=day|week`
- `/api/stats/activity-heatmap?chainId=8453&range=30d&dimension=hourOfWeek|mode`
- `/api/stats/activity-feed?chainId={8453,4217}&limit=50`
- `/api/tasks?chainId={8453,4217}&status={open,completed,cancelled,expired,claimed,pending_approval,worker_selected}&sort=newest&limit=100` — fully paginated (27 pages at first fetch)

Money fields are stablecoin smallest units (6 decimals); displayed dollars are
raw / 1,000,000.

Two things the fetch found, and the page says so instead of hiding them:

1. **Most endpoints are platform-wide.** `market/stats` reports the same
   `openTasks` (52) on both chains and only differs in `paidWorkers` and
   `activeWallets`. Breakdowns, time series, heatmap and feed return identical
   payloads for `chainId=8453` and `chainId=4217`. The page labels those panels
   platform-wide.
2. **The task list and the breakdowns disagree by 160 tasks.** Breakdowns
   reports 1,705 tasks (matching the time-series all-time total); the live
   `/api/tasks` list returns 1,545, with the gap entirely in `open` (192 vs 52),
   `claimed` (24 vs 6) and `worker_selected` (2 vs 0). The live open count of
   52 is confirmed twice (task list: 4 Base + 48 Tempo; `market/stats`:
   `openTasks` 52). The page shows the live count and records the gap in its
   methods section.

Every fetch writes a `verification` block into `data.json` (open-list vs
market-stats, mode total vs all-time created, status total vs mode total,
pagination completeness) and the page renders those checks.

## Refresh

- `.github/workflows/refresh.yml` — every hour: fetch, commit `data.json` if it
  changed, deploy to Pages. Data is cached at least 60 seconds; the page shows
  a visible "Data as of" time (UTC).
- `.github/workflows/pages.yml` — deploys to Pages on every push to `main`.

## Deploy (one command)

```sh
git push origin main
```

GitHub Actions builds and deploys to Pages automatically. No secrets, no
tokens, no server to run.

## Run locally

```sh
python3 scripts/fetch.py   # regenerates data.json from the live API
python3 -m http.server     # open http://localhost:8000
```

## License

MIT. Made for a paid Taskmarket bounty. Not an official Taskmarket product.
