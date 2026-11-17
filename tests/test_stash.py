from datetime import date
import unittest
from fantasy_basketball_manager.stash import assess, assess_own, plan, value_per_game, lead_days

TODAY = date(2026, 11, 17)
END = date(2027, 4, 11)


def player(pid, proj, last=None, now=None, games=0, status="OUT"):
    stats = [{"id": "102027", "appliedAverage": proj, "stats": {"42": 70}}]
    if last is not None:
        stats.append({"id": "002026", "appliedAverage": last, "stats": {"42": 70}})
    if now is not None:
        stats.append({"id": "002027", "appliedAverage": now, "stats": {"42": games}})
    return {"id": pid, "fullName": f"P{pid}", "injuryStatus": status, "stats": stats}


def back(day):
    return {"returnDate": day, "comment": ""}


class StashTests(unittest.TestCase):
    def test_value_ignores_tiny_current_sample(self):
        self.assertEqual(value_per_game(player(1, 40, 36, now=10, games=3), 2027), 38)
        self.assertAlmostEqual(value_per_game(player(1, 40, 36, now=29, games=6), 2027), 35)

    def test_far_away_star_is_watched_not_held(self):
        r = assess(player(1, 40, 40), back("2027-01-04"), TODAY, END, 26, 0.5, False, 0, 2027)
        self.assertTrue(r["action"].startswith("watch: pick up by"))
        self.assertEqual(r["pickUpBy"], "2026-12-27")       # 5 days + 3 for a star

    def test_around_the_corner_is_a_soft_stash(self):
        r = assess(player(1, 37, 35), back("2026-12-14"), TODAY, END, 26, 0.5, False, 0, 2027)
        self.assertEqual(r["action"], "stash now (first to drop)")
        # but not if a bench stash is already held
        r2 = assess(player(1, 37, 35), back("2026-12-14"), TODAY, END, 26, 0.5, False, 1, 2027)
        self.assertTrue(r2["action"].startswith("watch"))

    def test_inside_window_and_skip(self):
        r = assess(player(1, 37, 35), back("2026-11-20"), TODAY, END, 26, 0.5, False, 1, 2027)
        self.assertEqual(r["action"], "stash now, swapping out your current bench stash")
        self.assertEqual(assess(player(2, 28, 27), back("2026-11-20"), TODAY, END, 26, 0.5, False, 0, 2027)["action"], "skip")

    def test_lead_grows_with_league_activity(self):
        self.assertEqual((lead_days(30, 0.5), lead_days(30, 1.5), lead_days(30, 3), lead_days(45, 3)), (5, 7, 10, 13))

    def test_open_ir_allows_only_two_weeks_earlier(self):
        near = assess(player(1, 37, 35), back("2026-12-05"), TODAY, END, 26, 0.5, True, 1, 2027)
        far = assess(player(1, 37, 35), back("2027-03-01"), TODAY, END, 26, 0.5, True, 1, 2027)
        self.assertEqual(near["action"], "stash now (to IR)")
        self.assertTrue(far["action"].startswith("watch"))

    def test_stash_is_first_to_drop_for_a_better_hurt_player(self):
        stash, star = player(1, 36, 35), player(2, 45, 44, status="DAY_TO_DAY")
        r = assess_own(stash, back("2026-12-14"), TODAY, 12, 2027, [stash, star])
        self.assertTrue(r["firstToDrop"]); self.assertIn("drop first", r["advice"])
        calm = assess_own(stash, back("2026-12-14"), TODAY, 12, 2027, [stash])
        self.assertIn("first to drop if", calm["advice"])

    def test_plan_counts_bench_stash_and_ir(self):
        entries = [{"lineupSlotId": 13, "playerPoolEntry": {"player": player(5, 40, 40)}},
                   {"lineupSlotId": 12, "playerPoolEntry": {"player": player(6, 36, 35)}}]
        out = plan(entries, [player(9, 37, 36)], {9: back("2026-12-14")}, TODAY, END, 0.5, 2027, streamer=26)
        self.assertEqual((out["irOpen"], out["benchStashesHeld"]), (False, 1))
        self.assertTrue(out["freeAgents"][0]["action"].startswith("watch"))


if __name__ == "__main__":
    unittest.main()
