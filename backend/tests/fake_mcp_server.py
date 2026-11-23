"""A tiny but real MCP stdio server, used by the tests (JSON-RPC 2.0, one message per line)."""
import json
import sys

TOOLS = [
    {"name": "lookup_word", "description": "Define a word.",
     "inputSchema": {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "save_bookmark", "description": "Save a URL.",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "explode", "description": "Always fails.", "inputSchema": {"type": "object", "properties": {}}},
]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


print("fake server starting", file=sys.stderr, flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue  # notification
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-dictionary", "version": "1.0"}}})
    elif method == "tools/list":
        cursor = (msg.get("params") or {}).get("cursor")
        page = TOOLS[:2] if not cursor else TOOLS[2:]
        result = {"tools": page}
        if not cursor:
            result["nextCursor"] = "page2"
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments", {})
        if name == "lookup_word":
            text = f"{args['word']}: a word that means something."
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}})
        elif name == "save_bookmark":
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"saved {args['url']}"}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})
