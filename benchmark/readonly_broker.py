"""Read-only, journaled MCP broker for frozen retrieval comparisons.

This is an experimental contract diagnostic. Token counts use cl100k and are
accounting diagnostics, not provider usage or comparative efficiency evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
import sys

from agent_backend import query_command
def tokens(text):
    # Use the benchmark's frozen tokenizer lazily; importing retrieval also
    # imports its unrelated numpy based index implementation.
    from retrieval import tokens as retrieval_tokens
    return retrieval_tokens(text)


class BrokerError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Broker:
    def __init__(self, plan, arm_id, root: Path, graph_path: Path, journal: Path,
                 max_actions=4, runner=subprocess.run):
        self.plan = plan
        self.arm_id = arm_id
        self.root = Path(root).resolve(strict=True)
        self.graph_path = Path(graph_path)
        self.max_actions = max_actions
        self.runner = runner
        if type(max_actions) is not int or max_actions < 1:
            raise ValueError("max_actions must be a positive integer")
        comp = plan.get("comparison", {}) if isinstance(plan, dict) else {}
        self.ceiling = comp.get("shared_output_budget")
        if type(self.ceiling) is not int or not 1 <= self.ceiling <= 8000:
            raise ValueError("Invalid shared output budget")
        arms = [a for a in plan.get("arms", []) if isinstance(a, dict) and a.get("id") == arm_id]
        if len(arms) != 1:
            raise ValueError("Exactly one matching arm is required")
        arm = arms[0]
        self.binary = Path(arm.get("binary", ""))
        if not self.binary.is_absolute():
            self.binary = self.root / self.binary
        self.binary = self.binary.resolve(strict=True)
        expected = arm.get("binary_sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Missing pinned binary SHA-256")
        if hashlib.sha256(self.binary.read_bytes()).hexdigest() != expected:
            raise ValueError("Backend binary hash mismatch")
        self.manifest = plan.get("source_manifest")
        if not isinstance(self.manifest, dict) or not self.manifest:
            raise ValueError("Missing source manifest")
        for rel, digest in self.manifest.items():
            self._validate_rel(rel)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid source digest")
        self.journal = Path(journal)
        self.journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self._journal_fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as e:
            raise ValueError("Journal already exists; replay is disabled") from e
        os.fsync(self._journal_fd)
        dfd = os.open(self.journal.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        self._actions = 0
        self._failed_backend = False
        self._journal_poisoned = False
        self._lock = threading.RLock()

    @staticmethod
    def _validate_rel(path):
        if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
            raise BrokerError("Invalid relative path")
        p = PurePosixPath(path)
        if p.is_absolute() or any(part in ("", ".", "..") or part.startswith(".") for part in path.split("/")):
            raise BrokerError("Path is hidden, absolute, or traverses directories")
        return p

    def _append(self, value):
        if self._journal_poisoned:
            raise OSError("Journal is poisoned after a prior write failure")
        data = (_json(value) + "\n").encode("utf-8")
        try:
            view = memoryview(data)
            while view:
                n = os.write(self._journal_fd, view)
                if n <= 0:
                    raise OSError("Short journal write")
                view = view[n:]
            os.fsync(self._journal_fd)
        except OSError:
            self._journal_poisoned = True
            raise

    def close(self):
        """Close the exclusive journal descriptor after a broker session."""
        fd = getattr(self, "_journal_fd", None)
        if fd is not None:
            os.close(fd)
            self._journal_fd = None

    def _control_reply(self, message, reply):
        with self._lock:
            request_wire = _json(message) + "\n"
            self._append({"kind": "request", "request": message, "wire_line": request_wire,
                          "wire_bytes": len(request_wire.encode("utf-8")),
                          "diagnostic_tokens": len(tokens(request_wire))})
            reply_wire = _json(reply) + "\n"
            self._append({"kind": "control_reply", "reply": reply, "wire_line": reply_wire,
                          "wire_bytes": len(reply_wire.encode("utf-8")),
                          "diagnostic_tokens": len(tokens(reply_wire))})
        return reply

    def _path(self, rel):
        rel = self._normalize_source_path(rel)
        p = self._validate_rel(rel)
        if rel not in self.manifest:
            raise BrokerError("Path is outside the source manifest")
        target = self.root
        for part in p.parts:
            target = target / part
            if target.is_symlink():
                raise BrokerError("Symlink source paths are refused")
        try:
            resolved = target.resolve(strict=True)
        except OSError as e:
            raise BrokerError("Source path is unavailable") from e
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise BrokerError("Source path escapes root or is not a file")
        data = resolved.read_bytes()
        if hashlib.sha256(data).hexdigest() != self.manifest[rel]:
            raise BrokerError("Source bytes differ from frozen manifest")
        return data

    def _verify_inventory(self):
        eligible_ext = {".go", ".py", ".js", ".mjs", ".cjs", ".ts", ".md", ".mdx", ".rst", ".txt", ".tsx", ".jsx"}
        found = set()
        for candidate in self.root.rglob("*"):
            rel = candidate.relative_to(self.root)
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix not in eligible_ext:
                continue
            if any(part.startswith(".") or part in {"node_modules", "vendor", "graphify-out"} for part in rel.parts):
                continue
            found.add(rel.as_posix())
        if found != set(self.manifest):
            raise BrokerError("Eligible source inventory differs from frozen manifest")
        for source_path in self.manifest:
            self._path(source_path)

    def _normalize_source_path(self, path):
        if isinstance(path, str) and Path(path).is_absolute():
            try:
                path = Path(path).relative_to(self.root).as_posix()
            except ValueError as e:
                raise BrokerError("Absolute source path is outside the exact root") from e
        self._validate_rel(path)
        return path

    def _clip(self, text, budget):
        if len(tokens(text)) <= budget:
            return text, False
        accepted = []
        for row in text.splitlines():
            candidate = "\n".join([*accepted, row])
            if len(tokens(candidate)) > budget:
                break
            accepted.append(row)
        clipped = "\n".join(accepted)
        return clipped, True

    def _call_tool(self, name, args):
        if self._journal_poisoned:
            raise BrokerError("Journal unavailable")
        if self._failed_backend and name == "retrieve":
            raise BrokerError("Backend is unavailable after an infrastructure failure")
        if name == "retrieve":
            if set(args) - {"query", "budget"} or not isinstance(args.get("query"), str) or not args["query"].strip():
                raise BrokerError("retrieve requires nonempty query and optional budget")
            budget = args.get("budget", self.ceiling)
            if type(budget) is not int or not 1 <= budget <= self.ceiling:
                raise BrokerError("Budget must be within the shared output limit")
            self._verify_inventory()
            command_plan = dict(self.plan)
            command_plan["arms"] = [dict(item, binary=str(self.binary)) if isinstance(item, dict) and item.get("id") == self.arm_id else item
                                    for item in self.plan["arms"]]
            argv = query_command(command_plan, self.arm_id, self.root, self.graph_path, args["query"], budget, self.root)
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
            return {"content": [{"type": "text", "text": _json(record)}],
                    "isError": completed.returncode != 0,
                    "diagnostic_tokens": len(tokens(completed.stdout))}
        if name == "read":
            if set(args) - {"path", "start", "lines", "budget"}:
                raise BrokerError("Unknown read argument")
            rel = self._normalize_source_path(args.get("path"))
            start, lines, budget = args.get("start", 1), args.get("lines", 80), args.get("budget", self.ceiling)
            if type(start) is not int or start < 1 or type(lines) is not int or not 1 <= lines <= 200:
                raise BrokerError("Invalid source range")
            if type(budget) is not int or not 1 <= budget <= self.ceiling:
                raise BrokerError("Budget must be within the shared output limit")
            raw = self._path(rel)
            try:
                source = raw.decode("utf-8")
            except UnicodeError as e:
                raise BrokerError("Source file is not UTF-8") from e
            selected = source.splitlines()[start - 1:start - 1 + lines]
            full = "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start))
            clipped, was_clipped = self._clip(full, budget)
            payload = {"path": rel, "sha256": self.manifest[rel], "start": start, "requested_lines": lines,
                       "text": clipped, "clipped": was_clipped,
                       "content_tokens": len(tokens(clipped))}
            return {"content": [{"type": "text", "text": _json(payload)}], "isError": False,
                    "source": payload, "diagnostic_tokens": len(tokens(clipped))}
        raise BrokerError("Unknown tool")

    def handle(self, message):
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                     "error": {"code": -32600, "message": "Invalid JSON-RPC request"}}
            return self._control_reply(message, reply) if isinstance(message, dict) and message.get("method") == "tools/call" else reply
        method, ident = message["method"], message.get("id")
        if message.get("jsonrpc") != "2.0":
            self._actions += int(method == "tools/call")
            reply = {"jsonrpc": "2.0", "id": ident, "error": {"code": -32600, "message": "Invalid JSON-RPC request"}}
            return self._control_reply(message, reply)
        if "id" not in message and method != "tools/call":
            with self._lock:
                wire = _json(message) + "\n"
                self._append({"kind": "request", "request": message, "wire_line": wire,
                              "wire_bytes": len(wire.encode()), "diagnostic_tokens": len(tokens(wire))})
            return None
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "graf-readonly-broker", "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [
                {"name": "retrieve", "description": "Run the pinned retrieval backend.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "budget": {"type": "integer"}}, "required": ["query"], "additionalProperties": False}},
                {"name": "read", "description": "Read verified frozen source lines.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"}, "lines": {"type": "integer"}, "budget": {"type": "integer"}}, "required": ["path"], "additionalProperties": False}}]}
        elif method == "tools/call":
            with self._lock:
                self._actions += 1
                action = self._actions
                request_wire = _json(message) + "\n"
                self._append({"kind": "begin", "action": action, "request": message,
                              "wire_line": request_wire,
                              "wire_bytes": len(request_wire.encode("utf-8")),
                              "diagnostic_tokens": len(tokens(request_wire))})
                params = message.get("params")
                err = None
                if not isinstance(params, dict) or set(params) - {"name", "arguments", "_meta"} or not isinstance(params.get("name"), str):
                    err = "Invalid tools/call parameters"
                elif "_meta" in params and not isinstance(params["_meta"], dict):
                    err = "_meta must be an object"
                elif not isinstance(params.get("arguments", {}), dict):
                    err = "arguments must be an object"
                elif message.get("id") is None:
                    err = "tools/call notification has no response identifier"
                elif action > self.max_actions:
                    err = "Shared action limit exceeded"
                if err:
                    result = {"content": [{"type": "text", "text": err}], "isError": True}
                else:
                    try:
                        result = self._call_tool(params["name"], params.get("arguments", {}))
                    except (BrokerError, ValueError) as e:
                        text = {"error": str(e)}
                        if hasattr(self, "_backend_failure"):
                            text["backend_failure"] = self._backend_failure
                        result = {"content": [{"type": "text", "text": _json(text)}], "isError": True}
                reply = {"jsonrpc": "2.0", "id": ident, "result": result}
                serialized = _json(reply) + "\n"
                self._append({"kind": "reply", "action": action, "reply": reply,
                              "wire_line": serialized,
                              "wire_bytes": len(serialized.encode("utf-8")),
                              "diagnostic_tokens": len(tokens(serialized))})
                return reply if "id" in message else None
        elif method not in ("initialize", "ping", "tools/list"):
            return self._control_reply(message, {"jsonrpc": "2.0", "id": ident,
                                                 "error": {"code": -32601, "message": "Method not found"}})
        return self._control_reply(message, {"jsonrpc": "2.0", "id": ident, "result": result})

    def serve(self, input_stream, output_stream):
        """Serve newline-delimited JSON-RPC, recording exact input before parsing."""
        from provider_ledger import _parse_request
        for raw in input_stream:
            raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
            with self._lock:
                raw_record = {"kind": "raw_input", "raw_line": raw_bytes.decode("utf-8", errors="replace"),
                              "raw_hex": raw_bytes.hex(),
                              "wire_bytes": len(raw_bytes), "diagnostic_tokens": len(tokens(raw_bytes.decode("utf-8", errors="replace")))}
                self._append(raw_record)
            try:
                message = _parse_request(raw_bytes.strip())
            except Exception as exc:
                reply = {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": f"Parse error: {type(exc).__name__}"}}
                with self._lock:
                    wire = _json(reply) + "\n"
                    self._append({"kind": "control_reply", "reply": reply, "wire_line": wire,
                                  "wire_bytes": len(wire.encode("utf-8")),
                                  "diagnostic_tokens": len(tokens(wire))})
            else:
                reply = self.handle(message)
            if reply is not None:
                output_stream.write(_json(reply) + "\n")
                output_stream.flush()

