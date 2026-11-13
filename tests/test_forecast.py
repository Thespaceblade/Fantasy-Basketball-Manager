import unittest
from fantasy_basketball_manager.forecast import projected_rate, optimize


def player(pid, points, slots):
    return {"id": pid, "fullName": f"Player {pid}", "proTeamId": 1,
            "injuryStatus": "ACTIVE", "eligibleSlots": slots,
            "stats": [{"statSourceId": 1, "statSplitTypeId": 0, "seasonId": 2027,
                       "stats": {"0": points * 10, "42": 10}, "averageStats": {"40": 20}}]}


class ForecastTests(unittest.TestCase):
    def test_raw_stat_units(self):
        self.assertEqual(projected_rate(player(1, 30, [0]), 2027, [{"statId": 0, "points": 2}]), (60, 20))

    def test_no_actual_average_fallback(self):
        p = player(1, 30, [0]); p["stats"][0]["statSourceId"] = 0
        with self.assertRaisesRegex(ValueError, "No forward forecast"):
            projected_rate(p, 2027, [{"statId": 0, "points": 1}])

    def test_exact_eligible_slot_assignment(self):
        pro = {1: {"proGamesByScoringPeriod": {"10": [{"date": 1794585600000}]}}}
        players = [player(1, 30, [0, 11]), player(2, 25, [0]), player(3, 10, [11])]
        result = optimize(players, 10, pro, [0, 11], 2027, [{"statId": 0, "points": 1}])
        self.assertEqual(result["points"], 55)
        self.assertEqual({p["id"] for p in result["lineup"]}, {1, 2})
        self.assertEqual(len({p["slot"] for p in result["lineup"]}), 2)

    def test_minutes_apply_to_their_date_only(self):
        # November 13, 2026, noon UTC (same calendar date in New York).
        pro = {1: {"proGamesByScoringPeriod": {"10": [{"date": 1794571200000}]}}}
        args = ([player(1, 30, [0])], 10, pro, [0], 2027, [{"statId": 0, "points": 1}])
        same = optimize(*args, minutes={"date": "2026-11-13", "players": {"1": 30}})
        later = optimize(*args, minutes={"date": "2026-11-14", "players": {"1": 30}})
        self.assertEqual(same["points"], 45)
        self.assertEqual(later["points"], 30)


if __name__ == "__main__":
    unittest.main()
