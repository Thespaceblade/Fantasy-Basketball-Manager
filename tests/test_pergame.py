import unittest
from fantasy_basketball_manager.pergame import Params, game_logs, opponent_factors, predict, evaluate

RULES = [{"statId": 0, "points": 1}]


def game(period, game_id, points, minutes, team=1):
    stats = {"0": points, "40": minutes} if minutes else {}
    return {"statSourceId": 0, "statSplitTypeId": 5, "seasonId": 2027, "scoringPeriodId": period,
            "externalId": str(game_id), "proTeamId": team, "stats": stats}


def player(pid, games, ppg=20, mpg=30, team=1):
    pre = {"statSourceId": 1, "statSplitTypeId": 0, "seasonId": 2027,
           "stats": {"0": ppg * 10, "42": 10}, "averageStats": {"40": mpg}}
    return {"id": pid, "fullName": f"P{pid}", "proTeamId": team, "stats": [pre] + games}


class PerGameTests(unittest.TestCase):
    def test_did_not_play_rows_are_skipped(self):
        logs = game_logs([player(1, [game(1, 11, 30, 30), game(2, 12, 0, 0)])], 2027, RULES)
        self.assertEqual([r["period"] for r in logs[1]], [1])

    def test_uses_only_earlier_games(self):
        rows = game_logs([player(1, [game(1, 11, 30, 30), game(5, 15, 90, 30)])], 2027, RULES)[1]
        p = Params(decay=1, rate_prior_minutes=0, minutes_prior_games=0)
        self.assertAlmostEqual(predict(rows, (2 / 3, 30), 5, p)["points"], 30)

    def test_prior_shrinks_small_samples(self):
        rows = game_logs([player(1, [game(1, 11, 60, 30)])], 2027, RULES)[1]
        p = Params(decay=1, rate_prior_minutes=30, minutes_prior_games=0)
        # (prior 1.0/min × 30 pseudo-minutes + 60 points) / (30 + 30 minutes) = 1.5/min × 30 minutes
        self.assertAlmostEqual(predict(rows, (1.0, 30), 2, p)["points"], 45)

    def test_minutes_override_and_bounds(self):
        rows = game_logs([player(1, [game(1, 11, 30, 30)])], 2027, RULES)[1]
        p = Params(decay=1, rate_prior_minutes=0, minutes_prior_games=0)
        self.assertAlmostEqual(predict(rows, (1.0, 30), 2, p, minutes_override=20)["points"], 20)
        with self.assertRaises(ValueError):
            predict(rows, (1.0, 30), 2, p, minutes_override=61)

    def test_no_data_returns_none(self):
        self.assertIsNone(predict([], None, 3, Params()))

    def test_opponent_factor_is_relative_and_shrunk(self):
        logs = {1: [{"period": 1, "gameId": 1, "team": 1, "points": 40, "minutes": 20}],
                2: [{"period": 1, "gameId": 2, "team": 3, "points": 20, "minutes": 20}]}
        games = {1: (1, 2), 2: (3, 4)}
        f = opponent_factors(logs, games, 2, Params(opponent_prior_games=1))
        self.assertGreater(f[2], 1)          # team 2 allowed more than average
        self.assertLess(f[2], 4 / 3)         # but shrunk toward 1.0
        self.assertEqual(opponent_factors(logs, games, 1, Params()), {})  # nothing before period 1

    def test_backtest_compares_same_games(self):
        players = [player(1, [game(1, 11, 30, 30), game(2, 12, 30, 30), game(3, 13, 30, 30)], ppg=10)]
        schedule = [{"proGamesByScoringPeriod": {str(d): [{"id": 10 + d, "homeProTeamId": 1, "awayProTeamId": 2}]
                                                 for d in (1, 2, 3)}}]
        r = evaluate(players, schedule, 2027, RULES, {2, 3}, Params())["results"]
        self.assertEqual(r["perGame"]["games"], r["espnPreseason"]["games"])
        self.assertEqual(r["seasonAverage"]["meanAbsoluteError"], 0)
        self.assertEqual(r["espnPreseason"]["meanAbsoluteError"], 20)


if __name__ == "__main__":
    unittest.main()
