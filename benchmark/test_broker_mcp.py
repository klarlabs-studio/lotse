"""The MCP adapter must expose exactly the broker's tools and nothing else."""
import json
import unittest

from broker_mcp import TOOLS, handle
from task_session import TOOL_DEFINITIONS, tool_definitions


class FakeSession:
    def __init__(self, arm="graf"):
        self.arm = arm
        self.calls = []

    def call(self, operation, arguments):
        self.calls.append((operation, arguments))
        if operation == "edit":
            return {"ok": False, "error": "old must match exactly one occurrence"}
        return {"ok": True, "result": {"content": "L1: package cobra"}}


class BrokerMcpTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()

    def request(self, method, params=None, id=1):
        message = {"jsonrpc": "2.0", "id": id, "method": method}
        if params is not None:
            message["params"] = params
        return handle(message, self.session)

    def test_initialize_echoes_protocol_and_declares_tools_only(self):
        reply = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(set(reply["result"]["capabilities"]), {"tools"})

    def test_lists_exactly_the_broker_operations(self):
        tools = self.request("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], [t["name"] for t in tool_definitions("graf")])
        self.assertNotIn("outline", [t["name"] for t in tools])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertFalse(tool["inputSchema"].get("additionalProperties", True))

    def test_search_only_arm_has_no_retrieve_tool(self):
        self.session = FakeSession("search_only")
        tools = [t["name"] for t in self.request("tools/list")["result"]["tools"]]
        self.assertEqual(tools, ["read", "search", "edit", "test", "finish"])
        self.assertEqual(tools, [t["name"] for t in tool_definitions("search_only")])
        reply = self.request("tools/call", {"name": "retrieve", "arguments": {"query": "x"}})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(self.session.calls, [])

    def test_call_forwards_to_the_session_and_reports_errors(self):
        reply = self.request("tools/call", {"name": "read", "arguments": {"path": "args.go"}})
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(json.loads(reply["result"]["content"][0]["text"]), {"content": "L1: package cobra"})
        self.assertEqual(self.session.calls, [("read", {"path": "args.go"})])
        error = self.request("tools/call", {"name": "edit", "arguments": {"path": "a", "old": "x", "new": "y"}})
        self.assertTrue(error["result"]["isError"])
        self.assertIn("exactly one occurrence", error["result"]["content"][0]["text"])

    def test_unknown_tools_methods_and_notifications(self):
        unknown = self.request("tools/call", {"name": "shell", "arguments": {}})
        self.assertTrue(unknown["result"]["isError"])
        self.assertEqual(self.session.calls, [])
        self.assertEqual(self.request("resources/list")["error"]["code"], -32601)
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.session))
        self.assertEqual(self.request("ping")["result"], {})

    def test_graf_orient_lists_outline(self):
        self.session = FakeSession("graf_orient")
        tools = [t["name"] for t in self.request("tools/list")["result"]["tools"]]
        self.assertEqual(tools, ["retrieve", "read", "search", "edit", "outline", "test", "finish"])

    def test_schemas_cover_every_documented_parameter(self):
        for definition in TOOL_DEFINITIONS:
            self.assertEqual(set(TOOLS[definition["name"]]["properties"]), set(definition["parameters"]))


if __name__ == "__main__":
    unittest.main()
