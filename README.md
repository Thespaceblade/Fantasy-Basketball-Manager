# Fantasy Basketball Manager

Manage ESPN fantasy basketball from a command line or an AI agent: inspect rosters,
find available players and their stats, set daily lineups, run ESPN's Quick Lineup for
the rest of a matchup, make combined add/drop transactions, and compare points forecasts.

Includes **15 ESPN operations**, a Python CLI, an optional local MCP server, and an
installable agent skill. Requests run through [API Anything](https://github.com/goodnight000/api-anything).
Authentication stays on your computer. This repository contains no ESPN credentials,
private league snapshots, or personal transaction history. No ESPN API key is needed.

This is an unofficial integration with ESPN's website endpoints. These endpoints can
change. The original request flows were exercised on a live points league; generalized
templates have additional offline tests and a live read/projection smoke test. That
does not establish compatibility with every league type, lock setting, or season.

## Install

Requirements: **Python 3.10+**, **Node.js 22.13+**, npm/npx, Chrome for API Anything's login window, and an ESPN account that can
access the league. The CLI has no Python runtime dependencies; MCP is an optional extra.
API Anything is pinned to **0.4.0** because its saved-operation format is version-sensitive.

```bash
git clone https://github.com/Thespaceblade/Fantasy-Basketball-Manager.git
cd Fantasy-Basketball-Manager
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For MCP, install the extra instead:

```bash
python -m pip install -e '.[mcp]'
```

Configure your team, using the **ending year** of the ESPN season (2027 means 2026–27):

```bash
fbm configure --league-id YOUR_LEAGUE_ID --team-id YOUR_TEAM_ID --season 2027
npx -y api-anything@0.4.0 login fantasy-basketball-manager --window
fbm status
```

Replace the ID placeholders with numbers. The league ID is in the ESPN league URL;
your team URL contains `teamId`. Sign in within API Anything's own login window, then
close it so the session is saved. An unrelated browser tab being signed in does not
necessarily update that session. Never paste cookies into an issue or commit them.

`configure` writes non-secret settings to
`~/.config/fantasy-basketball-manager/config.json` and installs a separate operation
set at `~/.api-anything/sites/fantasy-basketball-manager.json`. It does not replace
an existing `espn-fantasy-basketball` operation set. Reinstall updated templates with
`fbm install-operations`.

For multiple teams, put `--config` **before** the command:

```bash
fbm --config ~/.config/fantasy-basketball-manager/another-team.json configure --league-id YOUR_LEAGUE_ID --team-id YOUR_TEAM_ID --season 2027
fbm --config ~/.config/fantasy-basketball-manager/another-team.json status
```

## First calls

```bash
fbm ops                           # Discover every supported ESPN operation
fbm status                        # Live roster, period, IR state, acquisition counters
fbm call lineup                   # Current day's roster and slots
fbm call availablePlayers         # All paged free-agent/waiver-pool entries and returned stats
fbm call matchups                 # Current scoreboard, teams and daily points
fbm project --output data/now.json # Day-by-day points forecast for both matchup teams
```

All results are JSON. Save private outputs under `data/`, which is gitignored.
`scoringPeriodId` is ESPN's sequential scoring-day ID, **not** the day of the month.
Omit it to resolve the live current period automatically. Use the schedule to map
future periods to dates; do not hardcode an old week's IDs.

## Complete ESPN call reference

League, team and season come from your config. `fbm call NAME key=value ...` supplies
the extra arguments below. All write commands preview by default; add `--apply` only
when you intend to send that exact action.

| Operation | Extra arguments | Result / behavior |
| --- | --- | --- |
| `league` | None | Teams, records, standings-related fields and transaction counters. |
| `leagueSnapshot` | None | Full season envelope: rosters, player stats, settings/scoring rules, schedule, status. |
| `matchups` | None | Current matchups, scores, daily points, and ESPN's returned roster detail. |
| `lineup` | `scoringPeriodId` optional | Configured team's roster and lineup slots for a day. |
| `availablePlayers` | `scoringPeriodId` optional; `offset` optional | FREEAGENT and WAIVERS entries, player IDs, eligibility, injuries, ownership, locks and returned stat splits. Automatically pages in batches of 1000 unless an explicit offset is supplied. Rank filters and URL use the same requested period. |
| `seasonMatchups` | None | Full selected-season matchup schedule, scores and daily point totals where ESPN returns them. |
| `draft` | None | Draft picks, players, teams and pick order where ESPN returns them. |
| `leagueActivity` | `startMs`, `endMs`; `offset` optional, default 0 | Up to 200 activity topics in a Unix-millisecond date range; each may have a limited message set. Page explicitly with offset. Not an exhaustive immutable transaction archive. |
| `nbaSchedule` | None | NBA team schedules and games by scoring period, including timestamps. |
| `seasonCalendar` | None | ESPN's embedded season constants: daily scoring dates and period boundaries. Resolves league week groupings into actual scoring-day IDs, including multi-week playoffs. |
| `quickLineupRestOfMatchup` | `scoringPeriodId` optional | **Write:** ESPN Quick Lineup → Rest of Matchup. Reads back each remaining day and flags healthy scheduled bench players for review. |
| `swapCurrentLineup` | `playerA`, `playerB`, `slotA`, `slotB`; `scoringPeriodId` optional | **Write:** Swap two eligible regular roster spots today. `slotA` and `slotB` are their current slots. |
| `swapFutureLineup` | Same as current swap; specify a future `scoringPeriodId` | **Write:** Swap two eligible regular roster spots later in the current matchup. |
| `addFreeAgent` | `playerId`; `scoringPeriodId` optional | **Write:** Add one verified FREEAGENT into an open regular-roster spot. |
| `addDropFreeAgent` | `playerId`, `dropPlayerId`; `scoringPeriodId` optional | **Write:** One combined add/drop transaction, never a standalone drop followed by an add. |

Additional commands:

| Command | Purpose |
| --- | --- |
| `fbm configure --league-id ID --team-id ID --season YEAR` | Save local settings and install operations. |
| `fbm install-operations` | Install/update the isolated API Anything site file; preserve its previous version locally. |
| `fbm ops` | List calls, descriptions, write flags and extra arguments. |
| `fbm status` | Read current period, league status, lineup and acquisition usage/limits. |
| `fbm project [--model pergame\|espn] [--exclude ID ...] [--minutes FILE] [--output FILE]` | Produce both teams' daily forecasts and selected starting players. The per-game model is the default. |
| `fbm backtest --from-period N --to-period M` | Score the per-game model, ESPN's preseason forecast and the season average against completed games. |
| `fbm compare ORIGINAL.json CURRENT.json` | Compare saved `fbm project` outputs by team ID and day, with point deltas and actual/forecast labels. |
| `fbm-mcp [--config PATH] [--allow-writes]` | Run the optional local stdio MCP server. |

Use `fbm --help` and `fbm call --help` for syntax. Modern season endpoints are used;
legacy seasons with a different ESPN API format are not supported.

### Lineup examples

```bash
# Preview, then apply the same current-matchup optimization.
fbm call quickLineupRestOfMatchup
fbm call quickLineupRestOfMatchup --apply

# Example IDs only: replace with current roster IDs and actual slots from lineup.
fbm call swapCurrentLineup playerA=111 playerB=222 slotA=12 slotB=11
fbm call swapCurrentLineup playerA=111 playerB=222 slotA=12 slotB=11 --apply

# Use the future period resolved from the live schedule.
fbm call swapFutureLineup playerA=111 playerB=222 slotA=12 slotB=11 scoringPeriodId=FUTURE_PERIOD
```

Relevant ESPN slots: PG=0, SG=1, SF=2, PF=3, C=4, G=5, F=6, UTIL=11,
bench=12, IR=13. Use each player's returned `eligibleSlots` and league settings;
not every league enables every slot. Manual swaps involving IR are intentionally
blocked because those moves need more eligibility handling.

Quick Lineup can return `[]` even when it changes slots. The tool checks fresh daily
rosters afterward. A healthy scheduled player on the bench is a review flag: on a full
slate, that may be correct. Injury changes after the call require a new check. It
sets lineups; it does not perform pickups or guarantee the maximum realized score.

### Acquisition examples

```bash
# One combined transaction. Preview first; send once only if intended.
fbm call addDropFreeAgent playerId=111 dropPlayerId=222
fbm call addDropFreeAgent playerId=111 dropPlayerId=222 --apply

# Add without a drop only when a regular roster slot is open.
fbm call addFreeAgent playerId=111
```

Writes read fresh roster, pool, period, acquisition limits, IR eligibility and locks.
The exact target request is previewed before a send; ESPN remains the final validator.
The local journal records intent before sending and readback afterward, under the
config directory as `transactions.local.jsonl`. There are **no automatic write retries**.
After an interrupted/uncertain response, inspect fresh ESPN state and the journal
before attempting anything else. If the requested add is already present and the drop
is absent, the tool returns `already_reflected` without another send.

Waiver claims/bids, trades, standalone drops, dropping undroppable/locked players,
and automatically fixing IR/capacity conflicts are not implemented. A blocker is
reported rather than resolved by dropping a different player. Acquisitions apply to
the current day; future lineup edits are limited to the current matchup. Historical
activity can be read without granting write access.

### History and pool examples

```bash
fbm call draft > data/draft.json
fbm call seasonMatchups > data/matchups.json
fbm call availablePlayers > data/available.json
fbm call leagueActivity startMs=1793491200000 endMs=1796083200000 offset=0
```

Create `data/` before shell redirection (`mkdir -p data`). The example timestamps are
a sample range; choose your own. Stats remain in ESPN's native structure:
`player.stats[]` includes `seasonId`, `scoringPeriodId`, `statSourceId`,
`statSplitTypeId`, raw `stats`, and sometimes `averageStats`, `appliedAverage`, and
`appliedTotal`. **Source 0 = actuals; source 1 = projections.** Distinguish season,
recent, and per-game splits rather than mixing them. Missing fields remain missing;
the pool is not a promise that ESPN supplies every possible historical stat.

## Projections and comparisons

```bash
fbm project --output data/start-of-week.json
# Later, after refreshing roster, schedule and forecasts:
fbm project --output data/current.json
fbm compare data/start-of-week.json data/current.json

# Injury scenario: player IDs to exclude on future dates.
fbm project --exclude 111 222
```

### Per-game model (default)

Each game is projected as **minutes × fantasy points per minute**, in your league's scoring:

- **Points per minute** come from the player's 2026-27 game log, weighted toward recent
  games (each older game counts 0.8× the next), and shrunk toward ESPN's preseason
  per-minute rate as if the preseason rate were 150 extra minutes of play.
- **Minutes** come from the same recency-weighted game log, or from a date-specific
  minutes file you supply (see below).
- Each prediction only uses games before the day being projected, so it can be backtested.

Game logs come from ESPN's public player endpoint (no league login is sent). Players
with no games yet fall back to ESPN's preseason forecast.

**Backtest (2026-27 season, run 2026-11-13).** Settings were tuned on Oct 29–Nov 6
(scoring periods 10–18) and then scored on later games the model had not seen.
Error is mean absolute error per player-game, in fantasy points; bias is the average
over- (+) or under- (−) projection. All three methods were scored on the same games.

| Games tested | Per-game model | ESPN preseason forecast | Season average to date |
| --- | --- | --- | --- |
| Nov 7–12 (715 games) | **10.1** error, +0.6 bias | 11.2 error, **+3.3 bias** | 10.2 error, −0.4 bias |
| Nov 9–12 (474 games) | 10.3 error, +0.3 bias | 11.4 error, +3.3 bias | **10.1** error, −0.7 bias |

What this means:

- ESPN's preseason forecast **overestimates by about 3 points per player per game**, which
  adds up to roughly 30 points per day for a full 10-player lineup. The per-game model
  removes most of that bias. This is the main reason it is the default.
- On single games, the per-game model is only about as accurate as a plain season average.
  A typical single game misses by about 10 fantasy points either way; no simple model gets
  much below that.
- **Minutes are the biggest remaining lever.** With perfect knowledge of each player's
  actual minutes, the same model's error drops from 10.1 to 8.5. Supplying a good
  day-of minutes forecast (`--minutes`) is the best way to improve today's projections.
- An opponent-strength adjustment (fantasy points allowed per minute) was tested and did
  not improve accuracy, so it is off by default (`Params.use_opponent`).

Rerun the backtest on your own league's scoring any time:

```bash
fbm backtest --from-period 19 --to-period 24
```

`--model espn` restores the previous behavior: ESPN forward season stat totals scored with
your league's rules and divided by projected games.

### Lineups, minutes and limits

For each date, the selected players come from a maximum-weight assignment into eligible
starting slots. Completed days use actual points. IR and players marked OUT/SUSPENSION
are excluded; DTD players are included unless explicitly excluded with `--exclude`.
Both teams are assumed to keep their current rosters. The model supports points leagues
and refuses position-specific scoring overrides that it cannot score correctly.

Optional date-specific minutes overrides use a local JSON file:

```json
{
  "date": "2026-11-13",
  "players": {"111": 30, "222": 24}
}
```

```bash
fbm project --minutes data/minutes.json --output data/current.json
```

Supplied minutes apply **only to games on that date**. Use a source you can access and
record its timestamp yourself; this release does not fetch RotoWire or paid forecasts.

These are point estimates, not win probabilities. Today's estimate covers full games
rather than remaining live-game production, and an OUT status today is held out for all
future dates until refreshed. Show injury scenarios and uncertainty instead of presenting
a forecast as a guarantee.

## Install the agent skill

After installing the CLI, copy the skill into your agent's skills directory. For Codex:

```bash
mkdir -p ~/.codex/skills
cp -R skills/fantasy-basketball-manager ~/.codex/skills/
```

Then ask: “Use fantasy-basketball-manager to inspect my matchup,” “compare the best
remaining streamers,” or “set Quick Lineup for the rest of the matchup, then verify.”
The skill calls the installed CLI/MCP tools; copying the skill alone does not install
the executable or sign you into ESPN. Other skill-compatible agents can use the same
folder in their own discovery directory.

## Optional MCP server

Install `.[mcp]` and configure a stdio MCP host with an **absolute** executable path:

```json
{
  "mcpServers": {
    "fantasy-basketball-manager": {
      "command": "/absolute/path/Fantasy-Basketball-Manager/.venv/bin/fbm-mcp",
      "args": []
    }
  }
}
```

On Windows use the corresponding `.venv/Scripts/fbm-mcp.exe` path. Reads and previews
are available by default; to expose execution, add `"--allow-writes"` to `args`.
This enables a tool, not blanket permission for the agent to change a roster.

| MCP tool | Inputs | Behavior |
| --- | --- | --- |
| `fantasy_operations` | None | List ESPN calls and arguments. |
| `fantasy_status` | None | Live state and acquisition counters. |
| `fantasy_read` | `operation`, optional `args` object | Run a named read operation. |
| `fantasy_project` | Optional `exclude_player_ids` array | Day-by-day points forecast and injury scenarios. |
| `fantasy_preview` | `operation`, optional `args` object | Preflight and preview a write; send nothing. |
| `fantasy_apply` | Same as preview | Available only with `--allow-writes`; preflight, preview, send once, read back. |

Example tool arguments:

```json
{"operation":"quickLineupRestOfMatchup","args":{}}
```

The wrapper invokes API Anything's CLI for writes; it does not depend on enabling
writes in a separately configured API Anything MCP server. The optional server uses
the [official MCP Python SDK's supported v1 line](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x), bounded below version 2.

## Troubleshooting and development

- **Missing login/session:** log in using the command above. The installed site pins
  `espn_s2` and `SWID` as login cookies so unrelated analytics cookies do not cause a
  false “not logged in” diagnosis. Never disclose their values.
- **IR / full roster / waiver / lock blocker:** inspect the returned state and ESPN;
  the tool will not choose a substitute drop or silently split a transaction.
- **Accepted but uncertain:** do not send again. Check `fbm call lineup` for the
  requested day and the local journal. Readback failure does not prove a write failed.
- **Backend drift:** API Anything's format is pinned. Relearn the changed request
  through an intercepted `--write` capture for writes, preview, and verify; do not
  disable interception or infer a transaction endpoint from a button label.
- **Historical gaps:** ESPN may omit a draft, individual daily roster, or old activity.
  The current endpoint family does not implement pre-2018 league history.

Run the offline suite (synthetic data only; it does not access your league):

```bash
python -m unittest discover -s tests -v
```

Optional MCP tests run when the extra is installed. Public operation templates are
hand-maintained sanitized versions of captured requests. Session headers, captures,
private fixtures, and credentials must never be added to the repository. The `data/`
directory and local config/journal paths are excluded from version control.

ESPN and NBA marks belong to their respective owners. This project is not affiliated
with ESPN.
