import copy
import json
from pathlib import Path
import tempfile
import unittest

from fantasy_basketball_manager.manager import Manager, counters, period_days


def entry(pid, slot=11, status="ACTIVE"):
    return {"playerId": pid, "lineupSlotId": slot, "pendingTransactionIds": None,
            "playerPoolEntry": {"id": pid, "onTeamId": 1, "status": "ONTEAM",
                "rosterLocked": False, "lineupLocked": False, "tradeLocked": False,
                "player": {"id": pid, "fullName": f"Player {pid}", "eligibleSlots": [0, 11, 12, 13],
                           "injuryStatus": status, "droppable": True, "proTeamId": 1}}}


def snapshot():
    return {"scoringPeriodId": 10, "status": {"currentMatchupPeriod": 2},
            "teams": [{"id": 1, "roster": {"entries": [entry(100), entry(200, 12)]},
                       "transactionCounter": {"acquisitions": 0, "matchupAcquisitionTotals": {"2": 0}}}],
            "settings": {"scheduleSettings": {"matchupPeriods": {"2": [2]}, "periodTypeId": 2},
                         "rosterSettings": {"lineupSlotCounts": {"11": 1, "12": 1, "13": 1}},
                         "acquisitionSettings": {"matchupAcquisitionLimit": 1,
                             "matchupLimitPerScoringPeriod": True, "acquisitionLimit": -1}}}


class FakeClient:
    def __init__(self):
        self.config = {"leagueId": "123", "teamId": 1, "season": 2027}
        self.snapshot = snapshot()
        self.pool = [{**entry(300)["playerPoolEntry"], "onTeamId": 0, "status": "FREEAGENT"}]
        self.sent = 0
        self.uncertain = False
        self.preview_wrong_player = False
        self.calendar = {"segments": [{"id": 0, "periodTypes": [{"id": 2,
            "periods": [{"id": 2, "scoringPeriodStart": 10, "scoringPeriodEnd": 11}]}]}]}

    def read(self, operation, **args):
        if operation == "leagueSnapshot":
            return copy.deepcopy(self.snapshot)
        if operation == "lineup":
            return copy.deepcopy(self.snapshot["teams"])
        if operation == "availablePlayers":
            return copy.deepcopy(self.pool)
        if operation == "nbaSchedule":
            return [{"id": 1, "proGamesByScoringPeriod": {"10": [{"id": 1}], "11": [{"id": 2}]}}]
        if operation == "seasonCalendar":
            return self.calendar
        raise AssertionError(operation)

    def call(self, operation, apply=False, **args):
        if not apply:
            if operation == "quickLineupRestOfMatchup":
                url = "https://lm-api-writes.fantasy.espn.com/apis/v3/games/fba/seasons/2027/segments/0/leagues/123/teams/1/recommendations?lineupScoringPeriodId=10&recommendationTypeId=LINEUP&restOfWeek=true"
                body = {}
            else:
                url = "https://lm-api-writes.fantasy.espn.com/apis/v3/games/fba/seasons/2027/segments/0/leagues/123/transactions/"
                body = {"teamId": 1, "scoringPeriodId": 10, "executionType": "EXECUTE", "type": "FREEAGENT",
                        "items": [{"playerId": 999 if self.preview_wrong_player else args["playerId"], "type": "ADD", "toTeamId": 1}]}
                if "dropPlayerId" in args:
                    body["items"].append({"playerId": args["dropPlayerId"], "type": "DROP", "fromTeamId": 1})
            return {"dry": True, "request": {"method": "POST", "url": url, "body": json.dumps(body)}}
        self.sent += 1
        if self.uncertain:
            raise RuntimeError("Response lost after send")
        if operation.startswith("add"):
            roster = self.snapshot["teams"][0]["roster"]["entries"]
            roster[:] = [e for e in roster if e["playerId"] != args.get("dropPlayerId")]
            roster.append(entry(args["playerId"]))
        return {"write": "accepted", "data": []}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = FakeClient()
        self.manager = Manager(self.client, Path(self.tmp.name) / "journal.jsonl")
        self.args = {"playerId": 300, "dropPlayerId": 100, "scoringPeriodId": 10}

    def test_preview_never_sends(self):
        result = self.manager.write("addDropFreeAgent", self.args)
        self.assertTrue(result["dry"])
        self.assertEqual(self.client.sent, 0)

    def test_combined_add_drop_sends_once_and_verifies(self):
        result = self.manager.write("addDropFreeAgent", self.args, apply=True)
        self.assertTrue(result["readback"]["verified"])
        self.assertEqual(self.client.sent, 1)
        result = self.manager.write("addDropFreeAgent", self.args, apply=True)
        self.assertEqual(result["status"], "already_reflected")
        self.assertEqual(self.client.sent, 1)

    def test_uncertain_send_is_not_retried(self):
        self.client.uncertain = True
        with self.assertRaises(RuntimeError):
            self.manager.write("addDropFreeAgent", self.args, apply=True)
        self.assertEqual(self.client.sent, 1)
        self.assertIn('"phase": "sending"', self.manager.journal.read_text())

    def test_waiver_player_is_not_claimed(self):
        self.client.pool[0]["status"] = "WAIVERS"
        with self.assertRaisesRegex(ValueError, "waiver"):
            self.manager.write("addDropFreeAgent", self.args, apply=True)
        self.assertEqual(self.client.sent, 0)

    def test_active_player_in_ir_blocks_acquisition(self):
        self.client.snapshot["teams"][0]["roster"]["entries"].append(entry(400, 13))
        with self.assertRaisesRegex(ValueError, "IR eligibility"):
            self.manager.write("addDropFreeAgent", self.args, apply=True)

    def test_regular_capacity_blocks_add_only(self):
        with self.assertRaisesRegex(ValueError, "full"):
            self.manager.write("addFreeAgent", {"playerId": 300}, apply=True)

    def test_limit_per_scoring_period_uses_matchup_length(self):
        self.assertEqual(counters(self.client.snapshot, 1, self.client.calendar)["matchupLimit"], 2)
        self.client.snapshot["teams"][0]["transactionCounter"]["matchupAcquisitionTotals"]["2"] = 2
        with self.assertRaisesRegex(ValueError, "limit reached"):
            self.manager.write("addDropFreeAgent", self.args, apply=True)

    def test_locked_drop_blocks_before_send(self):
        self.client.snapshot["teams"][0]["roster"]["entries"][0]["playerPoolEntry"]["lineupLocked"] = True
        with self.assertRaisesRegex(ValueError, "locked"):
            self.manager.write("addDropFreeAgent", self.args, apply=True)
        self.assertEqual(self.client.sent, 0)

    def test_mismatched_preview_blocks_send(self):
        self.client.preview_wrong_player = True
        with self.assertRaisesRegex(ValueError, "preview items"):
            self.manager.write("addDropFreeAgent", self.args, apply=True)
        self.assertEqual(self.client.sent, 0)

    def test_quick_lineup_empty_response_still_reads_every_day(self):
        result = self.manager.write("quickLineupRestOfMatchup", {}, apply=True)
        self.assertEqual([d["scoringPeriodId"] for d in result["readback"]["lineups"]], [10, 11])
        self.assertTrue(result["readback"]["needsBenchReview"])
        self.assertEqual(self.client.sent, 1)

    def test_past_period_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "today or later"):
            self.manager.write("quickLineupRestOfMatchup", {"scoringPeriodId": 9}, apply=True)

    def test_calendar_groups_multiweek_playoffs(self):
        self.client.snapshot["settings"]["scheduleSettings"]["matchupPeriods"]["2"] = [2, 3]
        self.client.calendar["segments"][0]["periodTypes"][0]["periods"].append(
            {"id": 3, "scoringPeriodStart": 12, "scoringPeriodEnd": 18})
        self.assertEqual(period_days(self.client.snapshot, 10, self.client.calendar), list(range(10, 19)))


if __name__ == "__main__":
    unittest.main()
