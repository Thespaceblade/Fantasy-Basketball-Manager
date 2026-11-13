"""Per-game fantasy point projections in the league's own scoring.

Each projection is minutes × fantasy points per minute × opponent factor:

* points per minute: the player's game log, weighted toward recent games, shrunk
  toward ESPN's preseason per-minute rate so a few games can't swing it too far;
* minutes: recent minutes (weighted the same way), shrunk toward ESPN's projected
  minutes, or a supplied minutes forecast (e.g. RotoWire) for that date;
* opponent factor: fantasy points per minute the opponent has allowed this season,
  relative to league average, shrunk toward 1.0 until the sample is large.

Only information from before the projected scoring period is used, so the same
code can be backtested honestly against completed games.

Default parameters were tuned on 2026-27 games from late October to early November
and checked on later games (see README, "Per-game projections"). The opponent factor
is off by default because it did not improve accuracy in that backtest.
"""
from dataclasses import dataclass, asdict
import json
from urllib.request import Request, urlopen

GAME_SPLIT = 5          # ESPN statSplitTypeId for single-game rows
MINUTES = "40"          # ESPN stat id for minutes
PUBLIC_PLAYERS = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"
                  "/segments/0/leaguedefaults/1?scoringPeriodId={period}&view=kona_player_info")


@dataclass(frozen=True)
class Params:
    decay: float = 0.8                  # weight multiplier per game further in the past
    rate_prior_minutes: float = 150.0   # pseudo-minutes of the preseason per-minute rate
    minutes_prior_games: float = 0.0    # pseudo-games of the preseason minutes
    opponent_prior_games: float = 8.0   # pseudo-games of an average defense
    use_opponent: bool = False


def fetch_players(season, period, ids=(), limit=700, timeout=60):
    """Public ESPN player pool with this season's game logs and preseason forecasts.

    Uses ESPN's public, unauthenticated player endpoint; no league cookies are sent.
    The most-owned `limit` players are fetched, plus any `ids` (e.g. both rosters).
    """
    stats = {"value": 82, "additionalValue": [f"00{season}", f"10{season}"]}
    filters = [{"players": {"limit": limit, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                            "filterStatsForTopScoringPeriodIds": stats}}]
    if ids:
        filters.append({"players": {"filterIds": {"value": sorted(set(ids))},
                                    "filterStatsForTopScoringPeriodIds": stats}})
    players = {}
    for f in filters:
        request = Request(PUBLIC_PLAYERS.format(season=int(season), period=int(period)),
                          headers={"x-fantasy-filter": json.dumps(f), "accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            for entry in json.loads(response.read().decode()).get("players", []):
                players[entry["player"]["id"]] = entry["player"]
    return list(players.values())


def score(stats, rules):
    if any(r.get("pointsOverrides") for r in rules):
        raise ValueError("Position-specific scoring overrides need a custom model")
    return sum(float(stats.get(str(r["statId"]), 0)) * r["points"] for r in rules)


def preseason(player, season, rules):
    """ESPN preseason forecast as (points per minute, minutes per game), or None."""
    stat = next((s for s in player.get("stats", []) if s.get("statSourceId") == 1
                 and s.get("statSplitTypeId") == 0 and s.get("seasonId") == season), None)
    if not stat:
        return None
    raw, mpg = stat.get("stats", {}), stat.get("averageStats", {}).get(MINUTES, 0)
    games = raw.get("42", 0)
    if games <= 0 or mpg <= 0:
        return None
    return score(raw, rules) / games / mpg, mpg


def game_logs(players, season, rules):
    """{playerId: [{period, gameId, team, points, minutes}]} for games actually played, oldest first."""
    logs = {}
    for p in players:
        rows = []
        for s in p.get("stats", []):
            if s.get("statSplitTypeId") != GAME_SPLIT or s.get("seasonId") != season:
                continue
            minutes = float(s.get("stats", {}).get(MINUTES, 0) or 0)
            if minutes <= 0:
                continue  # did not play: availability is handled by injury status, not this model
            rows.append({"period": s["scoringPeriodId"], "gameId": int(s["externalId"]),
                         "team": s["proTeamId"], "points": score(s["stats"], rules), "minutes": minutes})
        logs[p["id"]] = sorted(rows, key=lambda r: r["period"])
    return logs


def game_index(schedule):
    """{gameId: (homeTeamId, awayTeamId)} from ESPN proTeam schedules."""
    games = {}
    for team in schedule:
        for day in team.get("proGamesByScoringPeriod", {}).values():
            for g in day:
                games[g["id"]] = (g["homeProTeamId"], g["awayProTeamId"])
    return games


def opponent_factors(logs, games, before_period, params):
    """Fantasy points per minute each team allows, relative to league average, before a period."""
    allowed, team_games = {}, {}
    total_points = total_minutes = 0.0
    for rows in logs.values():
        for r in rows:
            if r["period"] >= before_period or r["gameId"] not in games:
                continue
            home, away = games[r["gameId"]]
            opp = away if r["team"] == home else home
            a = allowed.setdefault(opp, [0.0, 0.0])
            a[0] += r["points"]; a[1] += r["minutes"]
            team_games.setdefault(opp, set()).add(r["gameId"])
            total_points += r["points"]; total_minutes += r["minutes"]
    if total_minutes <= 0:
        return {}
    league = total_points / total_minutes
    factors = {}
    for team, (pts, mins) in allowed.items():
        n = len(team_games[team])
        raw = (pts / mins) / league
        factors[team] = 1 + (raw - 1) * n / (n + params.opponent_prior_games)
    return factors


def predict(rows, prior, before_period, params, opponent_factor=1.0, minutes_override=None):
    """Project one game. rows: that player's played games; prior: preseason (rate, mpg) or None."""
    past = [r for r in rows if r["period"] < before_period]
    w = [params.decay ** i for i in range(len(past) - 1, -1, -1)]
    w_points = sum(wi * r["points"] for wi, r in zip(w, past))
    w_minutes = sum(wi * r["minutes"] for wi, r in zip(w, past))
    w_games = sum(w)
    if prior:
        prior_rate, prior_mpg = prior
        rate_weight = params.rate_prior_minutes + w_minutes
        minutes_weight = params.minutes_prior_games + w_games
        rate = (prior_rate * params.rate_prior_minutes + w_points) / rate_weight if rate_weight else prior_rate
        mpg = (prior_mpg * params.minutes_prior_games + w_minutes) / minutes_weight if minutes_weight else prior_mpg
    elif past:
        rate, mpg = w_points / w_minutes, w_minutes / w_games
    else:
        return None
    minutes = float(minutes_override) if minutes_override is not None else mpg
    if not 0 <= minutes <= 60:
        raise ValueError("Minutes must be between 0 and 60")
    factor = opponent_factor if params.use_opponent else 1.0
    return {"points": rate * minutes * factor, "minutes": minutes, "perMinute": rate,
            "opponentFactor": factor, "gamesUsed": len(past)}


def evaluate(players, schedule, season, rules, periods, params):
    """Backtest against completed games in `periods`, using only earlier games for each prediction.

    Compares the per-game model with two simple baselines: ESPN's preseason points per
    game (what the matchup projection used before) and the season-to-date average.
    """
    logs, games = game_logs(players, season, rules), game_index(schedule)
    priors = {p["id"]: preseason(p, season, rules) for p in players}
    factors = {d: opponent_factors(logs, games, d, params) for d in periods}
    errors = {"perGame": [], "espnPreseason": [], "seasonAverage": []}
    for pid, rows in logs.items():
        prior = priors.get(pid)
        for r in rows:
            if r["period"] not in periods or r["gameId"] not in games:
                continue
            home, away = games[r["gameId"]]
            opp = away if r["team"] == home else home
            past = [x for x in rows if x["period"] < r["period"]]
            model = predict(rows, prior, r["period"], params, factors[r["period"]].get(opp, 1.0))
            if model is None or not prior or not past:
                continue  # compare all three methods on the same games only
            actual = r["points"]
            errors["perGame"].append(model["points"] - actual)
            errors["espnPreseason"].append(prior[0] * prior[1] - actual)
            errors["seasonAverage"].append(sum(x["points"] for x in past) / len(past) - actual)
    summary = {}
    for name, e in errors.items():
        if e:
            summary[name] = {"games": len(e), "meanAbsoluteError": round(sum(map(abs, e)) / len(e), 2),
                             "bias": round(sum(e) / len(e), 2)}
    return {"params": asdict(params), "periods": [min(periods), max(periods)], "results": summary}
