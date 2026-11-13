"""Local API Anything adapter. Authentication remains in its own session store."""
import json
import os
from pathlib import Path
import subprocess

SITE = "fantasy-basketball-manager"


class BackendError(RuntimeError):
    pass


class Client:
    def __init__(self, config):
        self.config = config

    def run(self, *args):
        proc = subprocess.run(
            ["npx", "-y", "api-anything@0.4.0", *map(str, args)],
            text=True, capture_output=True, check=False,
        )
        try:
            result = json.loads(proc.stdout)
        except ValueError as exc:
            raise BackendError("API Anything returned no JSON; check installation and login locally") from exc
        if not result.get("ok"):
            # Do not echo request bodies, headers or captured session data.
            raise BackendError(json.dumps({k: result.get(k) for k in
                                          ("class", "reason", "next", "write")}))
        return result

    def call(self, operation, *, apply=False, **args):
        spec = operation_spec(operation)
        names = {p["name"] for p in spec.get("params", [])}
        season = int(self.config["season"])
        supplied = {**{k: self.config[k] for k in ("leagueId", "teamId", "season")},
                    "offset": 0,
                    **{f"statId{i}": prefix + str(year) for i, (prefix, year) in enumerate(
                        [("00", season), ("10", season), ("00", season-1),
                         ("01", season), ("02", season), ("03", season), ("04", season)])},
                    **args}
        missing = names - supplied.keys()
        if missing:
            raise ValueError("Missing arguments: " + ", ".join(sorted(missing)))
        extra = args.keys() - names
        if extra:
            raise ValueError("Unknown arguments: " + ", ".join(sorted(extra)))
        command = ["call", SITE, operation]
        command += [f"{k}={supplied[k]}" for k in sorted(names)]
        command += ["--max-chars", "200000"]
        if not spec["readOnly"]:
            command += ["--allow-writes", "--max-tier", "1"]
            if not apply:
                command.append("--dry")
        result = self.run(*command)
        if result.get("truncated"):
            chunks, offset = [], 0
            while True:
                page = self.run("result", result["resultId"], "--format", "json",
                                "--max-chars", "200000", "--offset", offset)
                chunks.append(page["data"])
                if not page.get("more"):
                    result["data"] = json.loads("".join(chunks))
                    result.pop("truncated", None)
                    break
                new_offset = page["more"]["offset"]
                if new_offset <= offset:
                    raise BackendError("Result pagination did not advance")
                offset = new_offset
        return result

    def read(self, operation, **args):
        data = self.call(operation, **args)["data"]
        if operation != "availablePlayers" or "offset" in args:
            return data
        # ESPN returns at most 1000 pool entries per page. Never silently call it all players.
        rows = list(data)
        for offset in range(1000, 10000, 1000):
            if len(data) < 1000:
                return rows
            data = self.call(operation, offset=offset, **args)["data"]
            rows.extend(data)
        if len(data) == 1000:
            raise BackendError("Player-pool pagination exceeded 10000 rows; narrow the request")
        return rows


def templates():
    return json.loads(Path(__file__).with_name("operations.json").read_text())


def operation_spec(name):
    return next((o for o in templates()["operations"] if o["name"] == name), None) or _unknown(name)


def _unknown(name):
    raise ValueError("Unknown operation: " + name)


def install_operations():
    target = Path.home() / ".api-anything" / "sites" / (SITE + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # Preserve API Anything's previous site file for rollback, locally only.
        target.with_suffix(".json.bak").write_text(target.read_text())
    target.write_text(json.dumps(templates(), indent=2) + "\n")
    os.chmod(target, 0o600)
    return target
