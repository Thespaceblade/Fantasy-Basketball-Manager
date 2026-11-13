"""Fresh-state write preflight and readback; never retries writes."""
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs


def validate_preview(preview, operation, args, config):
    """Refuse a backend preview whose targets differ from the intended request."""
    if not preview.get("dry"):
        raise ValueError("Backend did not return a dry preview")
    request = preview["request"]
    url = urlsplit(request["url"])
    prefix = f"/apis/v3/games/fba/seasons/{config['season']}/segments/0/leagues/{config['leagueId']}"
    if request["method"] != "POST" or url.scheme != "https" or url.hostname != "lm-api-writes.fantasy.espn.com":
        raise ValueError("Unexpected write endpoint")
    body = json.loads(request.get("body") or "{}")
    if operation == "quickLineupRestOfMatchup":
        query = parse_qs(url.query)
        if url.path != prefix + f"/teams/{config['teamId']}/recommendations" or body != {} or any(
            query.get(k) != [str(v)] for k, v in {"lineupScoringPeriodId": args["scoringPeriodId"],
                "recommendationTypeId": "LINEUP", "restOfWeek": "true"}.items()):
            raise ValueError("Quick Lineup preview target differs from intent")
        return
    if url.path.rstrip("/") != prefix + "/transactions" or body.get("teamId") != config["teamId"] or body.get("scoringPeriodId") != args["scoringPeriodId"] or body.get("executionType") != "EXECUTE":
        raise ValueError("Transaction preview target differs from intent")
    items = body.get("items", [])
    if operation.startswith("add"):
        expected = [{"playerId": args["playerId"], "type": "ADD", "toTeamId": config["teamId"]}]
        if operation == "addDropFreeAgent":
            expected.append({"playerId": args["dropPlayerId"], "type": "DROP", "fromTeamId": config["teamId"]})
        if body.get("type") != "FREEAGENT" or items != expected:
            raise ValueError("Acquisition preview items differ from intended add/drop")
    else:
        expected = [{"playerId": args["playerA"], "type": "LINEUP", "fromLineupSlotId": args["slotA"], "toLineupSlotId": args["slotB"]},
                    {"playerId": args["playerB"], "type": "LINEUP", "fromLineupSlotId": args["slotB"], "toLineupSlotId": args["slotA"]}]
        kind = "ROSTER" if operation == "swapCurrentLineup" else "FUTURE_ROSTER"
        if body.get("type") != kind or items != expected:
            raise ValueError("Swap preview differs from intended players/slots")


def team(snapshot, team_id):
    return next(t for t in snapshot["teams"] if t["id"] == team_id)


def entries(snapshot, team_id):
    return team(snapshot, team_id)["roster"]["entries"]


def period_days(snapshot, period, calendar):
    # League matchupPeriods groups weekly periods (including multi-week playoffs),
    # not daily scoring IDs. Resolve those through ESPN's actual season calendar.
    if calendar.get("seasonConfig") is not None and calendar["seasonConfig"] != snapshot.get("seasonId", calendar["seasonConfig"]):
        raise ValueError("Calendar season does not match the league snapshot")
    settings = snapshot["settings"]["scheduleSettings"]
    segment = next(s for s in calendar["segments"] if s["id"] == snapshot.get("segmentId", 0))
    kind = next(t for t in segment["periodTypes"] if t["id"] == settings["periodTypeId"])
    periods = {p["id"]: p for p in kind["periods"]}
    for group in settings["matchupPeriods"].values():
        days = sorted({day for pid in group for day in range(
            periods[int(pid)]["scoringPeriodStart"], periods[int(pid)]["scoringPeriodEnd"] + 1)})
        if period in days:
            return days
    raise ValueError("Scoring period is missing from ESPN's league/season calendar")


def counters(snapshot, team_id, calendar):
    settings = snapshot["settings"]["acquisitionSettings"]
    counter = team(snapshot, team_id).get("transactionCounter", {})
    matchup = snapshot["status"]["currentMatchupPeriod"]
    used = counter.get("matchupAcquisitionTotals", {}).get(str(matchup))
    maximum = settings.get("matchupAcquisitionLimit", -1)
    if maximum >= 0 and settings.get("matchupLimitPerScoringPeriod"):
        maximum *= len(period_days(snapshot, snapshot["scoringPeriodId"], calendar))
    return {"matchupUsed": used, "matchupLimit": maximum,
            "matchupRemaining": None if maximum < 0 or used is None else max(0, maximum-used),
            "seasonUsed": counter.get("acquisitions"),
            "seasonLimit": settings.get("acquisitionLimit", -1)}


def lineup_entries(data, team_id):
    return next(t for t in data if t["id"] == team_id)["roster"]["entries"]


def summarize_lineup(data, team_id, schedule, period):
    pro = {p["id"]: p for p in schedule}
    rows, benched = [], []
    for e in lineup_entries(data, team_id):
        p = e["playerPoolEntry"]["player"]
        games = pro.get(p["proTeamId"], {}).get("proGamesByScoringPeriod", {}).get(str(period), [])
        slot = e["lineupSlotId"]
        row = {"id": p["id"], "name": p["fullName"], "slot": slot,
               "injuryStatus": p.get("injuryStatus"), "games": len(games),
               "eligible": slot in p["eligibleSlots"]}
        rows.append(row)
        if games and slot >= 12 and p.get("injuryStatus") == "ACTIVE":
            benched.append(row)
    return {"scoringPeriodId": period, "players": rows,
            "healthyScheduledBench": benched,
            "note": "Bench games require review when starting slots are full; OUT players are not assumed to recover."}


class Manager:
    def __init__(self, client, journal):
        self.client = client
        self.team_id = client.config["teamId"]
        self.journal = Path(journal)

    def record(self, record):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **record}) + "\n")

    def write(self, operation, args, apply=False):
        snapshot = self.client.read("leagueSnapshot")
        calendar = self.client.read("seasonCalendar")
        current = snapshot["scoringPeriodId"]
        period = args.get("scoringPeriodId")
        if period is None:
            period = args["scoringPeriodId"] = current
        if period < current or period not in period_days(snapshot, current, calendar):
            raise ValueError("Writes must target the current matchup, today or later")
        if operation in ("addFreeAgent", "addDropFreeAgent") and period != current:
            raise ValueError("Acquisitions must use the live current scoring period")
        roster = lineup_entries(self.client.read("lineup", scoringPeriodId=period), self.team_id)
        by_id = {e["playerId"]: e for e in roster}
        if operation.startswith("add"):
            add, drop = args["playerId"], args.get("dropPlayerId")
            if drop == add:
                raise ValueError("Add and drop players must differ")
            if add in by_id and (drop is None or drop not in by_id):
                return {"status": "already_reflected", "sent": False}
            if add in by_id or (drop is not None and drop not in by_id):
                raise ValueError("Add/drop roster does not match requested transaction")
            counts = counters(snapshot, self.team_id, calendar)
            if counts["matchupUsed"] is None:
                raise ValueError("Acquisition usage unknown; review on ESPN")
            if counts["matchupRemaining"] == 0:
                raise ValueError("Matchup acquisition limit reached")
            if counts["seasonLimit"] >= 0 and (counts["seasonUsed"] is None or counts["seasonUsed"] >= counts["seasonLimit"]):
                raise ValueError("Season acquisition limit reached or usage unknown")
            for e in roster:
                if e["lineupSlotId"] == 13:
                    p = e["playerPoolEntry"]["player"]
                    if p.get("injuryStatus") not in ("OUT", "INJURY_RESERVE") or 13 not in p["eligibleSlots"]:
                        raise ValueError("IR eligibility needs review on ESPN: " + p["fullName"])
            pool = self.client.read("availablePlayers", scoringPeriodId=period)
            candidate = next((p for p in pool if p["id"] == add), None)
            if candidate is None or candidate.get("status") != "FREEAGENT" or candidate.get("onTeamId", 0) != 0:
                raise ValueError("Player is not a verified free agent; waiver claims are unsupported")
            if any(candidate.get(k) for k in ("rosterLocked", "lineupLocked", "tradeLocked")):
                raise ValueError("Acquisition player is locked")
            if drop is not None:
                e = by_id[drop]
                ppe = e["playerPoolEntry"]
                if e.get("pendingTransactionIds") or any(ppe.get(k) for k in ("rosterLocked", "lineupLocked", "tradeLocked")) or not ppe["player"].get("droppable", False):
                    raise ValueError("Drop player is locked, undroppable, or has a pending transaction")
            else:
                capacity = sum(v for k, v in snapshot["settings"]["rosterSettings"]["lineupSlotCounts"].items() if int(k) < 13)
                if sum(e["lineupSlotId"] < 13 for e in roster) >= capacity:
                    raise ValueError("Regular roster is full; use combined add/drop")
        elif operation.startswith("swap"):
            expected = "swapCurrentLineup" if period == current else "swapFutureLineup"
            if operation != expected:
                raise ValueError("Use " + expected + " for this scoring period")
            a, b = args["playerA"], args["playerB"]
            if a == b or a not in by_id or b not in by_id:
                raise ValueError("Swap requires two different rostered players")
            for pid, from_slot, to_slot in [(a, args["slotA"], args["slotB"]), (b, args["slotB"], args["slotA"])]:
                e = by_id[pid]; ppe = e["playerPoolEntry"]
                if from_slot >= 13 or to_slot >= 13:
                    raise ValueError("IR moves require manual eligibility review; only regular-slot swaps supported")
                if e["lineupSlotId"] != from_slot or to_slot not in ppe["player"]["eligibleSlots"]:
                    raise ValueError("Swap slots do not match live roster or eligibility")
                if e.get("pendingTransactionIds") or ppe.get("lineupLocked") or ppe.get("rosterLocked"):
                    raise ValueError("Swap player is locked or has a pending transaction")
        elif operation != "quickLineupRestOfMatchup":
            raise ValueError("Unsupported write operation")
        preview = self.client.call(operation, apply=False, **args)
        validate_preview(preview, operation, args, self.client.config)
        if not apply:
            q = preview["request"]
            body = json.loads(q.get("body") or "{}")
            if "memberId" in body:
                body["memberId"] = "<saved-session-member>"
            return {"dry": True, "method": q["method"], "url": q["url"],
                    "body": body, "sent": False}
        # Record intent before sending. A failure or ambiguous response is never retried here.
        self.record({"operation": operation, "args": args, "phase": "sending"})
        result = self.client.call(operation, apply=True, **args)
        self.record({"operation": operation, "args": args, "phase": "response",
                     "write": result.get("write"), "status": result.get("data", {}).get("status") if isinstance(result.get("data"), dict) else None})
        if operation == "quickLineupRestOfMatchup":
            schedule = self.client.read("nbaSchedule")
            checks = [summarize_lineup(self.client.read("lineup", scoringPeriodId=day), self.team_id, schedule, day)
                      for day in period_days(snapshot, period, calendar) if day >= period]
            verification = {"lineups": checks,
                            "needsBenchReview": any(c["healthyScheduledBench"] for c in checks)}
        else:
            after = {e["playerId"]: e for e in lineup_entries(self.client.read("lineup", scoringPeriodId=period), self.team_id)}
            if operation.startswith("add"):
                verified = args["playerId"] in after and (args.get("dropPlayerId") is None or args["dropPlayerId"] not in after)
            else:
                verified = after.get(args["playerA"], {}).get("lineupSlotId") == args["slotB"] and after.get(args["playerB"], {}).get("lineupSlotId") == args["slotA"]
            verification = {"verified": verified}
        self.record({"operation": operation, "args": args, "phase": "readback", **verification})
        return {"write": result.get("write"), "readback": verification,
                "note": "Never resend an uncertain transaction; inspect fresh ESPN state first."}
