"""Streaming planner: rank free-agent add/drop pairs by lineup points added this matchup.

The value of a pickup is not the player's average; it is how many points he adds to
your *optimized starting lineup* on the remaining days, after losing whoever you drop.
A 25-point player who fills an empty starting slot on a light day beats a 40-point
player who would sit on the bench. This planner measures exactly that, using the
per-game model, and never sends anything.

Playbook rules applied:
* plan with the per-game model, not ESPN's preseason forecast (which ran ~3 pts/game high);
* rank by lineup points added across the remaining days, which naturally prefers
  players with games on your light days;
* keep one acquisition in reserve for injuries until the final two days of the matchup;
* supply a day-of minutes file (`--minutes`) when you have one: minutes are the
  biggest remaining source of single-game error.
"""
from .forecast import optimize, pergame_estimator

RESERVE_UNTIL_DAYS_LEFT = 2


def lineup_total(players, periods, pro, slots, season, rules, estimators, minutes=None):
    return sum(optimize(players, d, pro, slots, season, rules, minutes, None, estimators[d])["points"]
               for d in periods)


def candidate_value(player, periods, pro, estimator_for):
    """Rough standalone value (sum of projected games) used only to shortlist candidates."""
    total = 0.0
    for d in periods:
        for game in pro.get(player["proTeamId"], {}).get("proGamesByScoringPeriod", {}).get(str(d), []):
            try:
                total += estimator_for(d)(player, game, None)[0]
            except ValueError:
                return 0.0
    return total


def season_average(pool, season, rules):
    from .pergame import game_logs
    return {pid: sum(r["points"] for r in rows) / len(rows) for pid, rows in game_logs(pool, season, rules).items() if rows}


DOWNGRADE_MARGIN = 2.0  # fantasy points per game


def plan(roster, free_agents, periods, pro, slots, season, rules, pool, schedule,
         remaining_adds, minutes=None, shortlist=25, drop_shortlist=6, top=10, keep=(),
         allow_downgrade=False, first_drop_ids=()):
    """Return ranked add/drop pairs. `roster`: non-IR player dicts; `free_agents`: player dicts.

    Drops are chosen by the least lineup value lost this matchup; ties (e.g. players with no
    games left) go to the lowest season average, so a strong player is not cut just because
    he is idle this weekend. Player IDs in `keep` are never suggested as drops.

    Long-term guard: unless `allow_downgrade`, a pair is skipped when the dropped player's
    season average is more than DOWNGRADE_MARGIN above the added player's. One week of
    streaming points is not worth permanently swapping a starter for a streamer.

    `first_drop_ids` (injured bench stashes) are offered as drops first and are exempt from
    the downgrade guard: a stash is the first to go when you need the spot.
    """
    if not periods:
        return {"periods": [], "recommendations": [], "note": "No remaining days in this matchup."}
    estimators = {d: pergame_estimator(pool, schedule, season, rules, d) for d in periods}
    averages = season_average(pool, season, rules)
    base = lineup_total(roster, periods, pro, slots, season, rules, estimators, minutes)
    healthy = [p for p in free_agents if p.get("injuryStatus") not in ("OUT", "SUSPENSION")]
    adds = sorted(healthy, key=lambda p: -candidate_value(p, periods, pro, estimators.get))[:shortlist]
    # Drop candidates: the roster players whose removal costs the least lineup value.
    costs = []
    for p in roster:
        if p["id"] in set(keep):
            continue
        without = [q for q in roster if q["id"] != p["id"]]
        costs.append((base - lineup_total(without, periods, pro, slots, season, rules, estimators, minutes), p))
    first = set(first_drop_ids)
    costs.sort(key=lambda c: (c[1]["id"] not in first, round(c[0], 1), averages.get(c[1]["id"], 0)))
    drops = costs[:drop_shortlist]
    rows = []
    from .pergame import preseason

    def average(p):
        if p["id"] in averages:
            return averages[p["id"]]
        prior = preseason(p, season, rules)
        return prior[0] * prior[1] if prior else 0.0

    skipped = 0
    for fa in adds:
        for cost, d in drops:
            if not allow_downgrade and d["id"] not in first and average(d) > average(fa) + DOWNGRADE_MARGIN:
                skipped += 1
                continue
            team = [q for q in roster if q["id"] != d["id"]] + [fa]
            gain = lineup_total(team, periods, pro, slots, season, rules, estimators, minutes) - base
            if gain <= 0:
                continue
            games = [day for day in periods if pro.get(fa["proTeamId"], {}).get("proGamesByScoringPeriod", {}).get(str(day))]
            rows.append({"add": {"id": fa["id"], "name": fa["fullName"], "seasonAverage": round(average(fa), 1)},
                         "drop": {"id": d["id"], "name": d["fullName"], "lineupValueLost": round(cost, 1),
                                  "benchStash": d["id"] in first,
                                  "seasonAverage": round(average(d), 1)},
                         "lineupPointsAdded": round(gain, 1), "addGamesOnPeriods": games})
    rows.sort(key=lambda r: (-r["lineupPointsAdded"], r["drop"]["seasonAverage"]))
    # One row per free agent: the best drop for him. Alternatives add noise, not information.
    seen, unique = set(), []
    for r in rows:
        if r["add"]["id"] not in seen:
            seen.add(r["add"]["id"]); unique.append(r)
    rows = unique
    days_left = len(periods)
    usable = remaining_adds - (1 if remaining_adds > 0 and days_left > RESERVE_UNTIL_DAYS_LEFT else 0)
    return {"periods": periods, "baselineLineupPoints": round(base, 1), "remainingAcquisitions": remaining_adds,
            "recommendedToUseNow": max(usable, 0),
            "reserveAdvice": ("No acquisitions left this matchup: these moves cannot be made until the next one."
                              if remaining_adds <= 0 else
                              f"Keep 1 acquisition for injuries until {RESERVE_UNTIL_DAYS_LEFT} days remain."
                              if usable < remaining_adds else "No reserve needed."),
            "recommendations": rows[:top],
            "skippedLongTermDowngrades": skipped,
            "note": ("Each row is one add/drop evaluated on its own; after making one, rerun before the next. "
                     f"Pairs that drop a player averaging more than {DOWNGRADE_MARGIN:g} points/game above the add "
                     "are skipped unless --allow-downgrade. "
                     "Points added are model estimates (about ±10 per player-game). Nothing was sent.")}
