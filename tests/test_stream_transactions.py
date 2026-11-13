import unittest
from fantasy_basketball_manager.stream import plan
from fantasy_basketball_manager.transactions import parse, summarize

RULES = [{"statId": 0, "points": 1}]


def player(pid, ppg, team, slots=(0, 11)):
    pre = {"statSourceId": 1, "statSplitTypeId": 0, "seasonId": 2027,
           "stats": {"0": ppg * 10, "42": 10}, "averageStats": {"40": 30}}
    return {"id": pid, "fullName": f"P{pid}", "proTeamId": team, "injuryStatus": "ACTIVE",
            "eligibleSlots": list(slots), "stats": [pre]}


def schedule(games):
    """games: {team: [periods]} -> ESPN-style proTeams list."""
    return [{"id": t, "proGamesByScoringPeriod": {str(d): [{"id": t * 100 + d, "date": 1794585600000,
             "homeProTeamId": t, "awayProTeamId": 99}] for d in days}} for t, days in games.items()]


class StreamTests(unittest.TestCase):
    def setUp(self):
        # Two starting slots. Team 1 plays day 10 only; team 2 plays days 10 and 11; team 3 plays day 11.
        self.sched = schedule({1: [10], 2: [10, 11], 3: [11]})
        self.pro = {p["id"]: p for p in self.sched}
        self.roster = [player(1, 40, 1), player(2, 30, 1), player(3, 10, 1)]

    def run_plan(self, fas, remaining=2, keep=()):
        pool = self.roster + fas
        return plan(self.roster, fas, [10, 11], self.pro, [0, 11], 2027, RULES, pool, self.sched,
                    remaining, keep=keep)

    def test_light_day_filler_beats_better_benched_player(self):
        fas = [player(10, 45, 1), player(11, 20, 3)]  # 45-pt guy only plays day 10, where slots are full
        r = self.run_plan(fas)["recommendations"]
        self.assertEqual(r[0]["add"]["id"], 11)       # fills an empty day-11 slot
        self.assertEqual(r[0]["drop"]["id"], 3)       # cheapest drop
        self.assertAlmostEqual(r[0]["lineupPointsAdded"], 20, places=0)

    def test_keep_and_reserve(self):
        out = self.run_plan([player(11, 20, 3)], remaining=2, keep=(3,))
        self.assertNotIn(3, [r["drop"]["id"] for r in out["recommendations"]])
        self.assertEqual(out["recommendedToUseNow"], 2)        # only 2 days left: no reserve
        none_left = self.run_plan([player(11, 20, 3)], remaining=0)
        self.assertEqual(none_left["recommendedToUseNow"], 0)
        self.assertIn("No acquisitions left", none_left["reserveAdvice"])


class TransactionTests(unittest.TestCase):
    topics = [{"type": "ACTIVITY_TRANSACTIONS", "date": 1794586896404, "messages": [
                  {"messageTypeId": 178, "targetId": 7, "to": 6},
                  {"messageTypeId": 179, "targetId": 8, "to": 6},
                  {"messageTypeId": 239, "targetId": 9, "from": 13, "to": -1, "for": 6},
                  {"messageTypeId": 999, "targetId": 10, "to": 4}]},
              {"type": "MSG_BOARD", "date": 1794586896405, "messages": [{"messageTypeId": 178, "to": 6}]}]

    def test_parse_labels_and_filters(self):
        rows = parse(self.topics, {6: "Mine", 4: "Other"}, {7: "Add Guy", 8: "Drop Guy"})
        self.assertEqual(len(rows), 4)                          # message-board topic ignored
        actions = {r["playerId"]: r["action"] for r in rows}
        self.assertEqual(actions[7], "add"); self.assertEqual(actions[8], "drop")
        self.assertEqual(actions[9], "drop from IR")
        self.assertEqual(actions[10], "transaction type 999")  # unknown kept, labelled
        self.assertEqual({r["teamId"] for r in parse(self.topics, {}, {}, team_id=4)}, {4})

    def test_summary_counts(self):
        s = summarize(parse(self.topics, {6: "Mine", 4: "Other"}, {}))
        self.assertEqual(s["Mine"], {"adds": 1, "drops": 2})


if __name__ == "__main__":
    unittest.main()
