"""Experimental v3 broker avoiding the redundant pre-command inventory scan.

The guarded runner still checks artifact, binary, and full source inventory
immediately before and after every child process. These checks are not an
atomic filesystem snapshot.
"""
from __future__ import annotations

import json

from agent_backend import query_command
from readonly_broker import BrokerError, tokens, _json
from readonly_broker_v2 import Broker as V2Broker


class Broker(V2Broker):
    """V2 integrity contract with its retrieve-only redundant scan removed."""

    def _call_tool(self, name, args):
        if name != "retrieve":
            return super()._call_tool(name, args)

        # Keep the frozen v1 retrieval validation and error behavior. The
        # guarded runner performs inventory/pin checks after argv construction
        # and immediately before starting the backend.
        if self._journal_poisoned:
            raise BrokerError("Journal unavailable")
        if self._failed_backend:
            raise BrokerError("Backend is unavailable after an infrastructure failure")
        if set(args) - {"query", "budget"} or not isinstance(args.get("query"), str) or not args["query"].strip():
            raise BrokerError("retrieve requires nonempty query and optional budget")
        budget = args.get("budget", self.ceiling)
        if type(budget) is not int or not 1 <= budget <= self.ceiling:
            raise BrokerError("Budget must be within the shared output limit")

        command_plan = dict(self.plan)
        command_plan["arms"] = [dict(item, binary=str(self.binary)) if isinstance(item, dict) and item.get("id") == self.arm_id else item
                                for item in self.plan["arms"]]
        argv = query_command(command_plan, self.arm_id, self.root, self.graph_path,
                             args["query"], budget, self.root)
        try:
            completed = self.runner(argv, capture_output=True, text=True, timeout=60, check=False)
        except Exception as e:
            self._failed_backend = True
            stdout = getattr(e, "stdout", None) or getattr(e, "output", None)
            stderr = getattr(e, "stderr", None)
            self._backend_failure = {"type": type(e).__name__,
                                     "stdout": stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout,
                                     "stderr": stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr,
                                     "returncode": getattr(e, "returncode", None),
                                     "timeout": getattr(e, "timeout", None)}
            raise BrokerError(f"Backend infrastructure failure: {type(e).__name__}") from e
        record = {"argv": argv, "returncode": completed.returncode,
                  "stdout": completed.stdout, "stderr": completed.stderr}
        if completed.returncode != 0:
            self._failed_backend = True
        result = {"content": [{"type": "text", "text": _json(record)}],
                  "isError": completed.returncode != 0,
                  "diagnostic_tokens": len(tokens(completed.stdout))}
        # Preserve V2's exact Graphify no-match classification.
        if self._arm_for_checks == "graphify":
            try:
                parsed = json.loads(result["content"][0]["text"])
            except (ValueError, KeyError, IndexError, TypeError):
                return result
            if (parsed.get("returncode") == 4 and parsed.get("stdout") == "No matching nodes found.\n"
                    and parsed.get("stderr") == ""):
                result["isError"] = False
                result["outcome"] = "no_match"
                self._failed_backend = False
        return result
