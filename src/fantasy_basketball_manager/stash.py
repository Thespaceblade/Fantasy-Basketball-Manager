"""Injured-player stash timing: pick up as late as possible, but before anyone else would.

Nobody wants a bench full of injured stars. Every roster spot holding an injured player is
a spot you cannot stream with or use when one of your own players gets hurt. So the
question for a good injured free agent is not only "is he worth it?" but "when?":

* **Value**: his expected fantasy points per game (average of ESPN's projection, last
  season and this season, in your league's scoring), minus what a typical streamer gives
  you. Small surpluses are not worth a roster spot at all.
* **Timing**: ESPN publishes an estimated return date with each injury. The pickup window
  opens `lead` days before that date. Before the window, holding him costs a roster spot
  every day for nothing; inside it, waiting risks another team grabbing him.
* **Lead time** grows when other managers in your league add players often, and for
  stars, whom other managers watch more closely.
* **IR helps, a little.** A player listed OUT can sit in an open IR slot without costing a
  bench spot, so an open IR slot allows stashing up to two weeks earlier, but no more: the
  IR slot is also your cover when one of your own players gets hurt.
* **One bench stash at most**, for a player who is around the corner (back within about
  30 days). Past that, you are trading away your ability to react to injuries.
* **The stash is first to go.** If one of your own better players gets hurt and IR is
  full, drop the stash to make room before dropping anyone healthy. `fbm stream` also
  offers bench stashes as the first drop.

Return dates are ESPN estimates and often move. Re-run before acting.
"""
from datetime import date, timedelta
import json
from urllib.request import Request, urlopen

INJURY_URL = ("https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/{season}"
              "/athletes/{player}/injuries?lang=en&region=us")
GAMES_PER_DAY = 82 / 170      # an NBA team's games per calendar day over a season
MIN_SURPLUS = 5.0             # fantasy points per game above a streamer to be worth a spot
STAR = 40.0                   # points per game at which other managers watch closely
MAX_BENCH_STASHES = 1
LONG_ABSENCE_DAYS = 42        # own players out longer than this on the bench: consider dropping
SOFT_STASH_DAYS = 30          # one bench stash is fine for a player back within this many days
IR_EXTRA_DAYS = 14            # an open IR slot lets you stash this much earlier, but no earlier:
                              # IR is also your cover when one of your own players gets hurt


def fetch_injury(season, player_id, timeout=20):
    """Latest ESPN injury record for a player: status, comment and estimated return date."""
    def get(url):
        with urlopen(Request(url.replace("http://", "https://"), headers={"accept": "application/json"}),
                     timeout=timeout) as r:
            return json.loads(r.read().decode())
    items = get(INJURY_URL.format(season=int(season), player=int(player_id))).get("items", [])
    records = [get(i["$ref"]) for i in items[:3]]
    if not records:
        return None
    latest = max(records, key=lambda r: r.get("date", ""))
    ret = (latest.get("details") or {}).get("returnDate")
    return {"status": latest.get("status"), "updated": latest.get("date"),
            "returnDate": ret, "comment": latest.get("shortComment")}


def stat_avg(player, stat_id):
    s = next((x for x in player.get("stats", []) if x.get("id") == stat_id), None)
    return (s or {}).get("appliedAverage"), ((s or {}).get("stats") or {}).get("42", 0)


def value_per_game(player, season):
    """Average of ESPN's projection, last season and this season (if 5+ games), league scoring."""
    proj, _ = stat_avg(player, f"10{season}")
    last, _ = stat_avg(player, f"00{season - 1}")
    now, games = stat_avg(player, f"00{season}")
    parts = [v for v in (proj, last, now if games and games >= 5 else None) if v]
    return sum(parts) / len(parts) if parts else 0.0


def streamer_level(free_agents, season, top=10):
    """What a typical healthy streamer averages: mean of the top healthy free agents this season."""
    vals = sorted((stat_avg(p, f"00{season}")[0] or 0 for p in free_agents
                   if p.get("injuryStatus") in ("ACTIVE", None) and (stat_avg(p, f"00{season}")[1] or 0) >= 5),
                  reverse=True)[:top]
    return sum(vals) / len(vals) if vals else 0.0


def lead_days(value, other_adds_per_team_week):
    lead = 5 if other_adds_per_team_week < 1 else 7 if other_adds_per_team_week < 2 else 10
    return lead + (3 if value >= STAR else 0)


def assess(player, injury, today, season_end, streamer, adds_rate, ir_open, bench_stashes, season):
    value = value_per_game(player, season)
    surplus = value - streamer
    row = {"id": player["id"], "name": player["fullName"], "status": player.get("injuryStatus"),
           "valuePerGame": round(value, 1), "surplusOverStreamer": round(surplus, 1),
           "espnReturnEstimate": (injury or {}).get("returnDate"), "note": (injury or {}).get("comment")}
    if surplus < MIN_SURPLUS:
        return {**row, "action": "skip", "why": f"Only {surplus:.1f} pts/game better than a streamer"}
    if not injury or not injury.get("returnDate"):
        return {**row, "action": "watch", "why": "No ESPN return estimate yet; re-check after the next update"}
    back = date.fromisoformat(injury["returnDate"][:10])
    days_out = max((back - today).days, 0)
    lead = lead_days(value, adds_rate)
    pick_by = back - timedelta(days=lead)
    games_after = max((season_end - back).days, 0) * GAMES_PER_DAY
    row.update({"daysUntilReturn": days_out, "leadDays": lead, "pickUpBy": pick_by.isoformat(),
                "gamesLeftAfterReturn": round(games_after), "seasonValueGained": round(surplus * games_after)})
    eligible_ir = player.get("injuryStatus") == "OUT"
    if today >= pick_by:
        if bench_stashes >= MAX_BENCH_STASHES and not (ir_open and eligible_ir):
            return {**row, "action": "stash now, swapping out your current bench stash",
                    "why": f"In the pickup window, but you already hold {bench_stashes} bench stash(es)"}
        return {**row, "action": "stash now" + (" (to IR)" if ir_open and eligible_ir else ""),
                "why": f"Back in about {days_out} days; others may move within {lead} days of a return"}
    if bench_stashes < MAX_BENCH_STASHES and days_out <= SOFT_STASH_DAYS:
        return {**row, "action": "stash now (first to drop)",
                "why": (f"Back in about {days_out} days, so holding him is cheap; drop him first if one of "
                        "your better players gets hurt and needs the spot")}
    if ir_open and eligible_ir and today >= pick_by - timedelta(days=IR_EXTRA_DAYS):
        return {**row, "action": "stash now (to IR)",
                "why": f"Your IR slot is open and he is within {IR_EXTRA_DAYS} days of the pickup window"}
    return {**row, "action": f"watch: pick up by {pick_by.isoformat()}",
            "why": f"Out about {days_out} days; holding him before then wastes a roster spot for {(pick_by - today).days} days"}


def assess_own(player, injury, today, slot, season, better_hurt_without_ir=()):
    value = value_per_game(player, season)
    ret = (injury or {}).get("returnDate")
    days_out = max((date.fromisoformat(ret[:10]) - today).days, 0) if ret else None
    row = {"id": player["id"], "name": player["fullName"], "status": player.get("injuryStatus"),
           "slot": "IR" if slot == 13 else "bench/active", "valuePerGame": round(value, 1),
           "espnReturnEstimate": ret, "daysUntilReturn": days_out}
    if slot != 13 and player.get("injuryStatus") == "OUT":
        better = [b for b in better_hurt_without_ir if b["id"] != player["id"] and value_per_game(b, season) > value]
        if better:
            return {**row, "firstToDrop": True,
                    "advice": "drop first: " + ", ".join(b["fullName"] for b in better)
                              + " is hurt, better, and needs the spot (IR is full)"}
        if days_out is not None and days_out > LONG_ABSENCE_DAYS:
            return {**row, "firstToDrop": True,
                    "advice": "consider dropping or moving to IR: out a long time and blocking a spot"}
        return {**row, "firstToDrop": True,
                "advice": "hold as your bench stash; first to drop if a better player of yours gets hurt"}
    return {**row, "advice": "hold" + (" (on IR)" if slot == 13 else "")}


def plan(roster_entries, free_agents, injuries, today, season_end, adds_rate, season, top=12, streamer=None):
    """roster_entries: ESPN roster entries; free_agents: player dicts (healthy and injured);
    injuries: {playerId: record}. `streamer` overrides the streamer level computed from them."""
    ir_open = not any(e["lineupSlotId"] == 13 for e in roster_entries)
    own_hurt = [(e["playerPoolEntry"]["player"], e["lineupSlotId"]) for e in roster_entries
                if e["playerPoolEntry"]["player"].get("injuryStatus") in ("OUT", "DAY_TO_DAY")]
    bench_stashes = sum(1 for p, slot in own_hurt if slot != 13 and p.get("injuryStatus") == "OUT")
    streamer = streamer_level(free_agents, season) if streamer is None else streamer
    hurt_fas = [p for p in free_agents if p.get("injuryStatus") in ("OUT", "DAY_TO_DAY")]
    rows = [assess(p, injuries.get(p["id"]), today, season_end, streamer, adds_rate, ir_open, bench_stashes, season)
            for p in hurt_fas]
    order = {"stash": 0, "watch": 1, "skip": 2}
    rows.sort(key=lambda r: (order[r["action"].split()[0].rstrip(":")], -r.get("seasonValueGained", r["surplusOverStreamer"])))
    return {"today": today.isoformat(), "streamerLevel": round(streamer, 1), "irOpen": ir_open,
            "benchStashesHeld": bench_stashes, "otherTeamsAddsPerTeamPerWeek": round(adds_rate, 2),
            "yourInjuredPlayers": [assess_own(p, injuries.get(p["id"]), today, slot, season,
                                              [] if ir_open else [q for q, s in own_hurt if s != 13])
                                   for p, slot in own_hurt],
            "freeAgents": [r for r in rows if r["action"] != "skip"][:top],
            "note": "Return dates are ESPN estimates and move often. Re-run before acting; nothing was sent."}
