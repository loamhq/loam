"""The Hetzner Cloud tool for persona and engineer lanes."""

import json
import os
import sys

from mcp.server import MCPServer

sys.path.insert(0, os.environ["LOAM_LIB"])
import loam_hetzner  # noqa: E402

mcp = MCPServer("hetzner-cloud")


@mcp.tool(name="hetzner_api", description=(
    "Call the Hetzner Cloud API for a saved project. Use 'personal' for the "
    "shared key, or the name saved in Settings. Give a method and path such "
    "as GET /servers?per_page=50 or GET /pricing. For a write, pass the "
    "API's JSON object as body. Returns the HTTP status and JSON."))
def hetzner_api(project: str, method: str, path: str, body: dict | None = None) -> str:
    return json.dumps(loam_hetzner.call(project, method, path, body), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
