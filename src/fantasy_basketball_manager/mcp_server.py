"""Optional stdio MCP tools, read-only unless started with --allow-writes."""
import argparse
from pathlib import Path
from .cli import CONFIG, execute
from .client import operation_spec


def create_server(config_path=CONFIG, allow_writes=False):
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("Fantasy Basketball Manager")
    prefix = ["--config", str(config_path)]

    @server.tool()
    def fantasy_operations() -> list:
        """List all supported ESPN operations and their required extra arguments."""
        return execute([*prefix, "ops"])

    @server.tool()
    def fantasy_status() -> dict:
        """Read current roster, scoring period, matchup status and acquisition limits."""
        return execute([*prefix, "status"])

    @server.tool()
    def fantasy_read(operation: str, args: dict | None = None) -> dict:
        """Call a named read operation. Use fantasy_operations to discover its inputs."""
        if not operation_spec(operation)["readOnly"]:
            raise ValueError("Use fantasy_preview for write operations")
        data = execute([*prefix, "call", operation, *[f"{k}={v}" for k, v in (args or {}).items()]])
        return {"data": data}

    @server.tool()
    def fantasy_project(exclude_player_ids: list[int] | None = None) -> dict:
        """Project both teams day by day from forward ESPN forecasts; optionally exclude players."""
        args = [*prefix, "project"]
        if exclude_player_ids:
            args += ["--exclude", *map(str, exclude_player_ids)]
        return execute(args)

    @server.tool()
    def fantasy_preview(operation: str, args: dict | None = None) -> dict:
        """Fresh-state preflight and exact preview of a lineup or acquisition write; sends nothing."""
        if operation_spec(operation)["readOnly"]:
            raise ValueError("Use fantasy_read for read operations")
        return execute([*prefix, "call", operation, *[f"{k}={v}" for k, v in (args or {}).items()]])

    if allow_writes:
        @server.tool()
        def fantasy_apply(operation: str, args: dict | None = None) -> dict:
            """Execute an explicitly user-authorized write once after preflight, then read back ESPN state."""
            if operation_spec(operation)["readOnly"]:
                raise ValueError("Use fantasy_read for read operations")
            return execute([*prefix, "call", operation, *[f"{k}={v}" for k, v in (args or {}).items()], "--apply"])
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--allow-writes", action="store_true")
    args = parser.parse_args()
    create_server(args.config, args.allow_writes).run(transport="stdio")
