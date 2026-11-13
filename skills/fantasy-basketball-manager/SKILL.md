---
name: fantasy-basketball-manager
description: Manage ESPN fantasy basketball with live rosters, waiver-pool stats, lineup optimization, points forecasts, matchup history, and explicitly authorized player acquisitions. Uses the Fantasy Basketball Manager CLI or MCP server and API Anything's saved local session.
---

Use the installed `fbm` CLI or the Fantasy Basketball Manager MCP tools. Run `fbm ops`
or `fantasy_operations` to discover calls. The repository README contains installation
and the complete argument reference. League, team, and season come from the local
configuration; do not assume a particular user's IDs or dates.

Start with `fbm status` / `fantasy_status` and resolve the current scoring period,
matchup dates, roster, counters, and scoring rules from live ESPN data. ESPN's season
is the ending year: 2027 means 2026–27. A scoring period is not a day of the month.

For lineups, preview `quickLineupRestOfMatchup` first; execute when the user requested
it, then inspect every remaining day. The call returns an empty list even when slots
change. Verify healthy players with games are starting where capacity and eligibility
permit. With a full slate, compare projected points. Later injury news can invalidate
the initial arrangement. Quick Lineup does not acquire players.

For acquisition decisions, start with `fbm stream` / `fantasy_stream`: it ranks
free-agent add/drop pairs by lineup points added over the remaining matchup days, which
favors players who fill empty slots on light days. Follow its reserve advice (hold one
acquisition for injuries until two days remain), pass `--keep` for players the user
wants to hold, and rerun after every move. Then confirm the candidate's injury status,
schedule, starting-slot capacity, remaining acquisitions, and IR eligibility. ESPN may
refuse IR for a player listed OUT; check the IR slot actually accepts him before relying
on it. Add before the player's game locks; drop only after the dropped player's game ends.
Use `fbm transactions` / `fantasy_transactions` for league adds and drops by team. Distinguish FREEAGENT from WAIVERS. Waiver bids/claims are unsupported.
Use combined `addDropFreeAgent` when a drop is needed; never split it into a standalone
drop and later add. Do not substitute players without authorization.

All writes preview by default. `--apply` / `fantasy_apply` is only for the exact action
the user authorized. A general request for analysis does not authorize roster changes.
The manager reads live state, checks preconditions, previews, sends once, journals
intent/result, and reads back. Do not bypass a blocker or repeat an uncertain send.
Inspect ESPN and the local journal after interrupted/accepted calls. If add is already
present and drop absent, report it as already reflected. If Quick Lineup's bench review
is flagged, inspect whether starting slots are full before calling it a failure.

Use `fbm project --output data/current.json` for points forecasts and save dated
snapshots to compare later using `fbm compare`. The default per-game model uses recent
game logs shrunk toward ESPN's preseason rate, scored with the league's rules; it
removed ESPN's ~3-point-per-game overestimate in backtests (`fbm backtest`), but single
games still miss by about 10 points. `--model espn` gives the old preseason forecast. Explain that
future minutes, OUT exclusions, and DTD participation are assumptions; use exclusion
scenarios where needed. These forecasts are not calibrated win probabilities.
Completed days are actuals; today's estimate is a full-game forecast, not a remaining
live-game projection. Label the original forecast's date and method when comparing.

Keep ESPN cookies, captures, member IDs, private snapshots, and journals local. API
Anything owns login; never request passwords or display authentication material.
If private reads work but writes report missing login cookies, check the site's
`loginCookies` configuration for `espn_s2` and `SWID` before asking the user to sign in
again. Do not modify other sites or publish captured sessions.
