"""Command-line entry point; all account changes are previews unless --apply is given."""
import argparse
import json
import os
from pathlib import Path
import sys
from .client import Client, install_operations, operation_spec, templates
from .forecast import project
from .pergame import Params, evaluate, fetch_players
from .stream import plan as stream_plan
from .transactions import fetch_topics, parse, summarize
from .manager import period_days
from datetime import datetime
from zoneinfo import ZoneInfo
from .manager import Manager, counters

CONFIG = Path.home() / ".config" / "fantasy-basketball-manager" / "config.json"


def load_config(path):
    try:
        config = json.loads(Path(path).read_text())
    except FileNotFoundError as exc:
        raise ValueError("Run fbm configure --league-id ID --team-id ID --season YEAR first") from exc
    for k in ("leagueId", "teamId", "season"):
        if int(config[k]) <= 0:
            raise ValueError("Config values must be positive: " + k)
    config["leagueId"] = str(config["leagueId"])
    config["teamId"], config["season"] = int(config["teamId"]), int(config["season"])
    return config


def execute(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=CONFIG)
    sub = p.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure", help="Save non-secret local league settings and install API operations")
    setup.add_argument("--league-id", required=True, type=int)
    setup.add_argument("--team-id", required=True, type=int)
    setup.add_argument("--season", required=True, type=int, help="ESPN season-ending year, e.g. 2027 for 2026–27")
    sub.add_parser("install-operations", help="Install or update local API Anything operation templates")
    sub.add_parser("ops", help="List every operation and its arguments")
    call = sub.add_parser("call", help="Call a named operation (writes preview by default)")
    call.add_argument("operation")
    call.add_argument("args", nargs="*", help="Arguments as key=value")
    call.add_argument("--apply", action="store_true", help="Send an explicitly authorized write once after preflight")
    sub.add_parser("status", help="Live roster, matchup status, and acquisition counters")
    forecast = sub.add_parser("project", help="Project current matchup using forward stat forecasts")
    forecast.add_argument("--model", choices=("pergame", "espn"), default="pergame",
                          help="pergame (default): recent game logs + preseason prior; espn: ESPN preseason per-game forecast")
    forecast.add_argument("--minutes", type=Path, help="Optional JSON {date: YYYY-MM-DD, players: {ESPN_ID: minutes}}")
    test = sub.add_parser("backtest", help="Score the per-game model against completed games (public data, no login)")
    test.add_argument("--from-period", type=int, required=True, help="First daily scoring period to test")
    test.add_argument("--to-period", type=int, required=True, help="Last daily scoring period to test")
    forecast.add_argument("--exclude", type=int, nargs="*", default=[], help="Scenario: exclude these player IDs on future days")
    forecast.add_argument("--output", type=Path)
    history = sub.add_parser("transactions", help="League transaction history: adds and drops by team, newest first")
    history.add_argument("--days", type=int, default=30, help="How many days back to read (default 30)")
    history.add_argument("--team", type=int, help="Only this team ID")
    stream = sub.add_parser("stream", help="Rank free-agent add/drop pairs by lineup points added this matchup (read-only)")
    stream.add_argument("--from-period", type=int, help="First scoring period a pickup would count (default: today)")
    stream.add_argument("--minutes", type=Path, help="Optional day-of minutes JSON, same format as project --minutes")
    stream.add_argument("--top", type=int, default=10)
    stream.add_argument("--keep", type=int, nargs="*", default=[], help="Player IDs never to suggest dropping")
    compare = sub.add_parser("compare", help="Compare two saved fbm project outputs by team ID and scoring period")
    compare.add_argument("original", type=Path)
    compare.add_argument("current", type=Path)
    args = p.parse_args(argv)
    if args.command == "configure":
        if min(args.league_id, args.team_id, args.season) <= 0:
            p.error("IDs and season must be positive")
        config = {"leagueId": str(args.league_id), "teamId": args.team_id, "season": args.season}
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(json.dumps(config, indent=2) + "\n")
        os.chmod(args.config, 0o600)
        return {"config": str(args.config), "operations": str(install_operations()),
                "login": "npx -y api-anything@0.4.0 login fantasy-basketball-manager --window"}
    if args.command == "install-operations":
        return {"operations": str(install_operations())}
    if args.command == "ops":
        internal = {"leagueId", "teamId", "season", "offset", *[f"statId{i}" for i in range(7)]}
        return [{"name": o["name"], "write": not o["readOnly"], "description": o.get("description"),
                 "args": [p["name"] for p in o["params"] if p["name"] not in internal]}
                for o in templates()["operations"]]
    if args.command == "compare":
        old, new = (json.loads(path.read_text()) for path in (args.original, args.current))
        if old["matchupId"] != new["matchupId"]:
            raise ValueError("Cannot compare different matchups")
        originals = {t["teamId"]: t for t in old["teams"]}
        result = []
        for t in new["teams"]:
            o = originals[t["teamId"]]; days = {d["period"]: d for d in o["days"]}
            result.append({"teamId": t["teamId"], "original": o["total"], "current": t["total"],
                "change": round(t["total"] - o["total"], 2),
                "days": [{"period": d["period"], "original": days[d["period"]]["points"],
                          "current": d["points"], "actual": d["actual"],
                          "change": round(d["points"] - days[d["period"]]["points"], 2)} for d in t["days"]]})
        return result
    config = load_config(args.config)
    client = Client(config)
    if args.command == "status":
        s = client.read("leagueSnapshot")
        return {"scoringPeriodId": s["scoringPeriodId"], "status": s["status"],
                "counters": counters(s, config["teamId"], client.read("seasonCalendar")),
                "lineup": client.read("lineup", scoringPeriodId=s["scoringPeriodId"])}
    if args.command == "transactions":
        if args.days <= 0:
            raise ValueError("--days must be positive")
        snapshot = client.read("leagueSnapshot")
        end = int(datetime.now(ZoneInfo("America/New_York")).timestamp() * 1000)
        topics = fetch_topics(client, end - args.days * 86_400_000, end)
        team_names = {t["id"]: t.get("name", str(t["id"])) for t in snapshot["teams"]}
        ids = {m.get("targetId") for t in topics if t.get("type") == "ACTIVITY_TRANSACTIONS"
               for m in t.get("messages", []) if isinstance(m.get("targetId"), int)}
        players = {p["id"]: p["fullName"] for p in fetch_players(config["season"], snapshot["scoringPeriodId"], ids, limit=0)} if ids else {}
        rows = parse(topics, team_names, players, args.team)
        return {"days": args.days, "byTeam": summarize(rows), "transactions": rows}
    if args.command == "stream":
        snapshot, schedule, calendar = client.read("leagueSnapshot"), client.read("nbaSchedule"), client.read("seasonCalendar")
        current = snapshot["scoringPeriodId"]
        start = args.from_period or current
        if start < current:
            raise ValueError("--from-period cannot be in the past")
        periods = [d for d in period_days(snapshot, current, calendar) if d >= start]
        mine = next(t for t in snapshot["teams"] if t["id"] == config["teamId"])
        roster = [e["playerPoolEntry"]["player"] for e in mine["roster"]["entries"] if e["lineupSlotId"] != 13]
        entries = [e for e in client.read("availablePlayers", scoringPeriodId=current) if e.get("status") == "FREEAGENT"]
        free_agents = [e["player"] for e in entries[:200]]  # ESPN sorts by percent rostered
        pool = fetch_players(config["season"], current, [p["id"] for p in roster + free_agents], limit=0)
        slots = [int(k) for k, n in snapshot["settings"]["rosterSettings"]["lineupSlotCounts"].items() if int(k) < 12 for _ in range(n)]
        remaining = counters(snapshot, config["teamId"], calendar)["matchupRemaining"]
        return stream_plan(roster, free_agents, periods, {p["id"]: p for p in schedule}, slots, config["season"],
                           snapshot["settings"]["scoringSettings"]["scoringItems"], pool, schedule,
                           remaining if remaining is not None else 99,
                           json.loads(args.minutes.read_text()) if args.minutes else None, top=args.top,
                           keep=args.keep)
    if args.command == "backtest":
        if not 0 < args.from_period <= args.to_period:
            raise ValueError("Periods must be positive and in order")
        snapshot = client.read("leagueSnapshot")
        pool = fetch_players(config["season"], snapshot["scoringPeriodId"])
        return evaluate(pool, client.read("nbaSchedule"), config["season"],
                        snapshot["settings"]["scoringSettings"]["scoringItems"],
                        set(range(args.from_period, args.to_period + 1)), Params())
    if args.command == "project":
        snapshot = client.read("leagueSnapshot")
        pool = None
        if args.model == "pergame":
            ids = [e["playerId"] for t in snapshot["teams"] for e in t["roster"]["entries"]]
            pool = fetch_players(config["season"], snapshot["scoringPeriodId"], ids)
        result = project(snapshot, client.read("nbaSchedule"), client.read("seasonCalendar"), config["teamId"],
                         config["season"], json.loads(args.minutes.read_text()) if args.minutes else None,
                         set(args.exclude), pool)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        return result
    spec = operation_spec(args.operation)
    supplied = {}
    for value in args.args:
        key, sep, val = value.partition("=")
        if not sep or key in supplied:
            raise ValueError("Arguments must be unique key=value pairs")
        param = next((x for x in spec["params"] if x["name"] == key), None)
        if param is None:
            raise ValueError("Unknown argument: " + key)
        if key in ("leagueId", "teamId", "season") or key.startswith("statId"):
            raise ValueError("Use a separate --config to change league, team, or season")
        supplied[key] = int(val) if param["type"] == "number" else val
    if not spec["readOnly"]:
        journal = args.config.parent / "transactions.local.jsonl"
        return Manager(client, journal).write(args.operation, supplied, apply=args.apply)
    if args.apply:
        raise ValueError("--apply is only for write operations")
    if "scoringPeriodId" in {x["name"] for x in spec["params"]} and "scoringPeriodId" not in supplied:
        supplied["scoringPeriodId"] = client.read("leagueSnapshot")["scoringPeriodId"]
    return client.read(args.operation, **supplied)


def main():
    try:
        print(json.dumps(execute(), indent=2))
    except (ValueError, RuntimeError, KeyError, StopIteration, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "note": "No automatic write retries. Inspect ESPN state after any uncertain send."}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
