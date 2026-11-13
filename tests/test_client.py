import asyncio
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import urlsplit
from fantasy_basketball_manager.client import Client, templates


class ClientTests(unittest.TestCase):
    def test_arguments_bound_to_requested_period_and_season(self):
        client = Client({"leagueId": "123", "teamId": 2, "season": 2028})
        with patch("fantasy_basketball_manager.client.subprocess.run") as run:
            run.return_value = SimpleNamespace(stdout='{"ok":true,"data":[]}', returncode=0)
            client.read("availablePlayers", scoringPeriodId=44)
        command = run.call_args.args[0]
        self.assertIn("scoringPeriodId=44", command)
        self.assertIn("statId0=002028", command)
        self.assertIn("statId2=002027", command)

    def test_full_pool_pages_beyond_1000(self):
        client = Client({"leagueId": "123", "teamId": 2, "season": 2028})
        with patch.object(client, "call", side_effect=[{"data": list(range(1000))}, {"data": [1000]}]) as call:
            self.assertEqual(len(client.read("availablePlayers", scoringPeriodId=44)), 1001)
            self.assertEqual(call.call_args.kwargs["offset"], 1000)

    def test_only_cookie_reference_survives_in_templates(self):
        for op in templates()["operations"]:
            self.assertEqual(op["trigger"]["steps"], [])
            self.assertFalse(any(k.lower() in ("authorization", "cookie") for k in op["request"]["headers"]))
            for slot in op["slots"]:
                if "ref" in slot:
                    self.assertEqual(slot["ref"], "cookie:SWID")

    def test_url_templates_and_target_bindings_are_consistent(self):
        for op in templates()["operations"]:
            if op["name"] == "seasonCalendar":
                self.assertEqual(op["response"]["format"], "embedded")
                self.assertIn({"param": "season", "at": ["query:seasonId"]}, op["slots"])
                continue
            path = urlsplit(op["request"]["url"]).path.lstrip("/").split("/")
            self.assertEqual(path[:5], ["apis", "v3", "games", "fba", "seasons"])
            targets = {"season": "path:5"}
            if op["name"] != "nbaSchedule":
                self.assertEqual(path[6:9], ["segments", "0", "leagues"])
                self.assertEqual(path[9], "0")
                targets["leagueId"] = "path:9"
            if op["name"] == "quickLineupRestOfMatchup":
                self.assertEqual(path[10:12], ["teams", "0"])
                targets["teamId"] = "path:11"
            for param, location in targets.items():
                self.assertIn({"param": param, "at": [location]}, op["slots"])
                self.assertIn(param, {p["name"] for p in op["params"]})


class MCPTests(unittest.TestCase):
    def test_write_tool_is_opt_in(self):
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError:
            self.skipTest("MCP optional extra not installed")
        from fantasy_basketball_manager.mcp_server import create_server
        for enabled in (False, True):
            tools = asyncio.run(create_server(allow_writes=enabled).list_tools())
            names = {t.name for t in tools}
            self.assertIn("fantasy_preview", names)
            self.assertEqual("fantasy_apply" in names, enabled)


if __name__ == "__main__":
    unittest.main()
