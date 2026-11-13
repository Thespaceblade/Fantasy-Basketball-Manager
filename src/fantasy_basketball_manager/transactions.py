"""Readable league transaction history from ESPN's league activity feed."""
from datetime import datetime
from zoneinfo import ZoneInfo

# Message types observed in ESPN basketball league activity. Unknown transaction
# types are still reported, labelled with their numeric ID, rather than dropped.
ACTIONS = {178: "add", 179: "drop", 239: "drop from IR", 180: "waiver add", 181: "waiver drop"}
PAGE = 200  # leagueActivity page size


def team_of(message):
    """ESPN puts the acting team in `for` for some message types and `to` for others."""
    for key in ("for", "to"):
        value = message.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def parse(topics, team_names, player_names, team_id=None):
    rows = []
    for topic in topics:
        if topic.get("type") != "ACTIVITY_TRANSACTIONS" or topic.get("isDeleted"):
            continue
        for m in topic.get("messages", []):
            team = team_of(m)
            if team_id is not None and team != team_id:
                continue
            pid = m.get("targetId")
            rows.append({
                "date": datetime.fromtimestamp(topic["date"] / 1000, ZoneInfo("America/New_York")).isoformat(timespec="minutes"),
                "teamId": team, "team": team_names.get(team, str(team)),
                "action": ACTIONS.get(m.get("messageTypeId"), f"transaction type {m.get('messageTypeId')}"),
                "playerId": pid, "player": player_names.get(pid, str(pid)),
            })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def summarize(rows):
    by_team = {}
    for r in rows:
        t = by_team.setdefault(r["team"], {"adds": 0, "drops": 0})
        if "add" in r["action"]:
            t["adds"] += 1
        elif "drop" in r["action"]:
            t["drops"] += 1
    return dict(sorted(by_team.items(), key=lambda kv: -kv[1]["adds"]))


def fetch_topics(client, start_ms, end_ms, max_pages=20):
    topics = []
    for page in range(max_pages):
        batch = client.read("leagueActivity", startMs=start_ms, endMs=end_ms, offset=page * PAGE)
        topics += batch
        if len(batch) < PAGE:
            return topics
    raise RuntimeError("Activity feed did not end after %d pages; narrow the date range" % max_pages)
