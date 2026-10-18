"""Expose one prepared trial's task broker to an MCP client over stdio.

    python benchmark/broker_mcp.py TASK TRIAL_ID --arm graf|graphify [--catalog FILE]

The model sees only the broker operations in task_session.TOOL_DEFINITIONS.
Every call goes through Session.call, which records tool events outside the
model's workspace. Newline-delimited JSON-RPC 2.0; no third-party SDK.
"""
import argparse
import json
from pathlib import Path
import sys

from agent_trials import BASE, tasks
from task_session import TOOL_DEFINITIONS, Session

BUDGET = {"type": "integer", "minimum": 1, "maximum": 8000, "default": 2000}
TOOLS = {
    "retrieve": {"properties": {"query": {"type": "string", "minLength": 1}, "budget": BUDGET},
                 "required": ["query"]},
    "read": {"properties": {"path": {"type": "string"}, "start": {"type": "integer", "minimum": 1, "default": 1},
                            "lines": {"type": "integer", "minimum": 1, "maximum": 200, "default": 80},
                            "budget": BUDGET},
             "required": ["path"]},
    "search": {"properties": {"pattern": {"type": "string", "minLength": 1, "maxLength": 256}, "budget": BUDGET},
               "required": ["pattern"]},
    "edit": {"properties": {"path": {"type": "string"}, "old": {"type": "string", "minLength": 1},
                            "new": {"type": "string"}},
             "required": ["path", "old", "new"]},
    "test": {"properties": {}, "required": []},
    "finish": {"properties": {}, "required": []},
}


def _tool(definition):
    schema = TOOLS[definition["name"]]
    return {"name": definition["name"], "description": definition["description"],
            "inputSchema": {"type": "object", **schema, "additionalProperties": False}}


def _text(value, is_error):
    return {"content": [{"type": "text", "text": value if isinstance(value, str) else
                         json.dumps(value, ensure_ascii=False)}], "isError": is_error}


def handle(message, session):
    """Answer one JSON-RPC message; notifications get no reply."""
    method, params = message.get("method"), message.get("params") or {}
    if "id" not in message:
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "graf-task-broker", "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [_tool(d) for d in TOOL_DEFINITIONS]}
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            result = _text(f"Unknown tool: {name}", True)
        else:
            response = session.call(name, params.get("arguments") or {})
            result = _text(response["result"], False) if response["ok"] else _text(response["error"], True)
    else:
        return {"jsonrpc": "2.0", "id": message["id"],
                "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task")
    parser.add_argument("trial_id")
    parser.add_argument("--arm", required=True, choices=["graf", "graphify"])
    parser.add_argument("--catalog", type=Path, default=BASE / "benchmark/agent-tasks.json")
    args = parser.parse_args()
    task = next(t for t in tasks(args.catalog) if t["id"] == args.task)
    session = Session(task, args.trial_id, args.arm)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line), session)
        except Exception as error:  # Malformed input must not kill the trial silently.
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
