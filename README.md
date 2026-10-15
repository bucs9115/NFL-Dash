# 🏈 Gridiron Leaders — NFL Statistics Dashboard

Madden-style NFL leaderboards and game breakdowns. It's a fully static site: a Python script builds `data/stats.json` from [nflverse](https://github.com/nflverse) data via `nflreadpy`, GitHub Actions refreshes it every week, and Netlify hosts it. There's no backend.

```
├── index.html                      # Dashboard shell (Tailwind CDN + custom Madden CSS)
├── app.js                          # Rendering, search, tabs, matchup modal
├── fetch_stats.py                  # nflverse → data/stats.json pipeline
├── data/stats.json                 # Generated data (committed)
├── requirements.txt
├── netlify.toml
└── .github/workflows/update_stats.yml
```

## Features
- **League Leaderboards**: top 5 in 50+ metrics across Passing, Rushing, Receiving, OL, DL/Edge, LB, Secondary, Special Teams and Team. You can view season-to-date or any single week.
- **Traditional / Advanced toggle**: advanced view shows EPA, pressures, target share, drops, broken tackles, coverage allowed and OL win rates.
- **Game Matchup Hub**: every completed game, with a FINAL badge and records. Click a game for quarter scores, team stats and head-to-head leaders.
- **Instant search** (press `/`): highlights players or teams across all cards.
- **Layered avatars**: team logo behind an ESPN headshot cutout, with a ring in the team's color. Badges show Rookie, IR and Suspended status.
- **Multiple seasons**: the season list comes from whatever the script generated.

## Local development
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python fetch_stats.py                 # current season + 2 prior
SEASONS=2022,2023,2024 python fetch_stats.py   # custom (PowerShell: $env:SEASONS="2022,2023")
python -m http.server 8000            # open http://localhost:8000
```
> Open the page through a local server, not `file://`, because browsers block `fetch()` on local files.

## Deploy
### 1. GitHub
```bash
git init && git add . && git commit -m "Initial dashboard"
git branch -M main
git remote add origin https://github.com/bucs9115/NFL-Dash.git
git push -u origin main
```
In the repo, go to **Settings → Actions → General → Workflow permissions** and select **Read and write permissions** so the bot can push the refreshed JSON.

### 2. Netlify
1. **Add new site → Import an existing project → GitHub**, then pick the repo.
2. Leave the build command empty and set the publish directory to `.` (`netlify.toml` already does this).
3. Deploy. Every push to `main` redeploys automatically, including the weekly data commits.

### 3. Automation
`.github/workflows/update_stats.yml` runs **every Tuesday at 09:00 UTC**, after Monday Night Football. It installs dependencies, runs `fetch_stats.py`, checks the JSON and commits it only if something changed. The commit triggers a Netlify redeploy. To run it by hand, go to **Actions → Update NFL Stats → Run workflow**. You can enter seasons there to backfill.

## Data notes
| Area | Source |
|---|---|
| Box score, EPA, kicking, returns | `load_player_stats(summary_level="week")` |
| Broken tackles, drops, pressures, coverage allowed | `load_pfr_advstats` (PFR, 2018+) |
| Team totals, red zone, time of possession, net punting, quarter scores | `load_pbp` |
| Finals and records | `load_schedules` |
| Status, rookies, ESPN headshot IDs | `load_rosters_weekly`, `load_players` |

- **PBWR\* / RBWR\*** are proxies built from play-by-play data. ESPN's real win rates come from player tracking and aren't public. PBWR\* is the share of dropbacks with no sack and no QB hit. RBWR\* is the share of designed runs that gain 4+ yards or a first down.
- Rate stats have minimum volumes that scale with the number of weeks, e.g. 15 pass attempts per week and 8 carries per week.
- Season-to-date covers the regular season only. Playoff weeks can be picked one at a time.
- DL/LB/DB grouping follows nflverse `position_group`, so some edge rushers listed as OLB show up under Linebackers.
