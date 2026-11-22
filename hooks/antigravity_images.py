"""Route native Antigravity image requests to the Images MCP tool."""

import json
import sys


if __name__ == "__main__":
    call = json.load(sys.stdin)["toolCall"]
    native_image = call["name"] == "generate_image" or (
        call["name"] == "invoke_subagent"
        and (call.get("args") or {}).get("TypeName") == "image-generator"
    )
    if native_image:
        print(json.dumps({"decision": "deny", "reason":
                          "Images is on. Use the images MCP server's generate_image tool."}))
    else:
        print(json.dumps({"decision": "allow"}))
