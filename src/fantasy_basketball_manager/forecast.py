"""Forward stat forecasts scored with the league's rules, with eligible-slot optimization."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .manager import period_days


def projected_rate(player, season, rules):
    stat = next((s for s in player.get("stats", []) if s.get("statSourceId") == 1
                 and s.get("statSplitTypeId") == 0 and s.get("seasonId") == season), None)
    if stat is None:
        raise ValueError("No forward forecast for " + player["fullName"])
    if any(r.get("pointsOverrides") for r in rules):
        raise ValueError("Position-specific scoring overrides need a custom model")
    raw = stat["stats"]
    games, minutes = raw.get("42", 0), stat.get("averageStats", {}).get("40", 0)
    if games <= 0 or minutes <= 0 or any(str(r["statId"]) not in raw for r in rules):
        raise ValueError("Incomplete forward forecast for " + player["fullName"])
    points = sum(raw[str(r["statId"])] * r["points"] for r in rules) / games
    return points, minutes


def espn_estimator(season, rules):
    """The original estimator: ESPN's preseason points per game (or per-minute rate × supplied minutes)."""
    def estimate(player, game, override):
        points, mpg = projected_rate(player, season, rules)
        if override is not None:
            return points / mpg * float(override), "ESPN projected rate × supplied minutes"
        return points, "ESPN season forecast"
    return estimate


def pergame_estimator(players, schedule, season, rules, period, params=None):
    """Per-game model: recent game logs shrunk toward ESPN's preseason rate (see pergame.py)."""
    from .pergame import Params, game_logs, game_index, opponent_factors, preseason, predict
    params = params or Params()
    logs, games = game_logs(players, season, rules), game_index(schedule)
    priors = {p["id"]: preseason(p, season, rules) for p in players}
    factors = opponent_factors(logs, games, period, params) if params.use_opponent else {}

    def estimate(player, game, override):
        pid = player["id"]
        opp = game.get("awayProTeamId") if game.get("homeProTeamId") == player["proTeamId"] else game.get("homeProTeamId")
        result = predict(logs.get(pid, []), priors.get(pid) or preseason(player, season, rules), period, params,
                         factors.get(opp, 1.0), override)
        if result is None:
            raise ValueError("No game log or forecast for " + player["fullName"])
        source = "per-game model" + (" × supplied minutes" if override is not None else "")
        return result["points"], source
    return estimate


def optimize(players, period, pro, slots, season, rules, minutes=None, excluded=None, estimator=None):
    estimator = estimator or espn_estimator(season, rules)
    dp = {0: (0.0, [])}
    for p in players:
        if p["id"] in (excluded or set()) or p.get("injuryStatus") in ("OUT", "SUSPENSION"):
            continue
        games = pro.get(p["proTeamId"], {}).get("proGamesByScoringPeriod", {}).get(str(period), [])
        if not games:
            continue
        value, sources = 0.0, []
        for game in games:
            day = datetime.fromtimestamp(game["date"] / 1000, ZoneInfo("America/New_York")).date().isoformat()
            override = (minutes or {}).get("players", {}).get(str(p["id"])) if day == (minutes or {}).get("date") else None
            if override is not None and not 0 <= float(override) <= 60:
                raise ValueError("Minutes override must be between 0 and 60")
            points, source = estimator(p, game, override)
            value += points
            sources.append(source)
        if value <= 0:
            continue
        update = dp.copy()
        for mask, (score, picked) in dp.items():
            for i, slot in enumerate(slots):
                if slot in p["eligibleSlots"] and not mask & (1 << i):
                    nxt = mask | (1 << i)
                    if nxt not in update or score + value > update[nxt][0]:
                        update[nxt] = (score + value, picked + [{"id": p["id"], "name": p["fullName"],
                            "slot": slot, "points": round(value, 2), "sources": sources}])
        dp = update
    score, chosen = max(dp.values(), key=lambda x: x[0])
    return {"points": round(score, 2), "starts": len(chosen), "lineup": chosen}


def project(snapshot, schedule, calendar, team_id, season, minutes=None, excluded=None, player_pool=None):
    """Project the current matchup. With `player_pool` (public players with game logs), uses the
    per-game model; otherwise ESPN's preseason per-game forecast."""
    current = snapshot["scoringPeriodId"]
    matchup_id = snapshot["status"]["currentMatchupPeriod"]
    matchup = next(m for m in snapshot["schedule"] if m["matchupPeriodId"] == matchup_id
                   and team_id in [(m.get("home") or {}).get("teamId"), (m.get("away") or {}).get("teamId")])
    if snapshot["settings"]["scoringSettings"].get("scoringType") not in ("H2H_POINTS", "TOTAL_POINTS"):
        raise ValueError("Points projections require a points league")
    pro = {p["id"]: p for p in schedule}
    slots = [int(k) for k, count in snapshot["settings"]["rosterSettings"]["lineupSlotCounts"].items()
             if int(k) < 12 for _ in range(count)]
    rules = snapshot["settings"]["scoringSettings"]["scoringItems"]
    results = []
    for key in ("home", "away"):
        side = matchup[key]
        team = next(t for t in snapshot["teams"] if t["id"] == side["teamId"])
        players = [e["playerPoolEntry"]["player"] for e in team["roster"]["entries"] if e["lineupSlotId"] != 13]
        days = []
        for period in period_days(snapshot, current, calendar):
            if period < current:
                actual = side.get("pointsByScoringPeriod", {}).get(str(period))
                if actual is None:
                    raise ValueError("Completed scoring-period actual is missing")
                days.append({"period": period, "points": actual, "actual": True})
            else:
                estimator = (pergame_estimator(player_pool, schedule, season, rules, period)
                             if player_pool is not None else None)
                days.append({"period": period, "actual": False,
                    **optimize(players, period, pro, slots, season, rules, minutes, excluded, estimator)})
        results.append({"teamId": team["id"], "name": team.get("name", " ".join(
            str(team.get(k, "")) for k in ("location", "nickname")).strip()),
            "scoreSoFar": side["totalPoints"], "total": round(sum(d["points"] for d in days), 2), "days": days})
    return {"retrievedAt": datetime.now(ZoneInfo("America/New_York")).isoformat(),
            "matchupId": matchup_id, "currentPeriod": current, "teams": results,
            "model": "pergame" if player_pool is not None else "espn",
            "method": ("Per-game model: recency-weighted game logs shrunk toward ESPN's preseason per-minute rate, "
                       "scored with league rules" if player_pool is not None else
                       "Forward ESPN season stat forecasts scored with league rules")
                      + "; maximum-weight eligible-slot assignment. Completed days use actual points.",
            "limitations": ["No calibrated win probability. Single games are noisy: the per-game model's backtest error is about 10 fantasy points per player-game.",
                "Current day is a full-game forecast, not a live remaining-game model.",
                "OUT players and IR are excluded on every future date; DTD players are included unless explicitly excluded.",
                "Assumes no further roster changes by either team; future minutes use season projections unless a date-matched override is supplied."]}
