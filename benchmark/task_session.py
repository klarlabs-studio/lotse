"""Isolated tool broker for paired repair trials. Never invokes a solver itself.

Only this process reads the private task catalog; a model receives the issue and
TOOL_DEFINITIONS and requests operations through JSONL. No arbitrary shell tool.
All model usage must be captured by a separately configured solver adapter.
"""
import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib

from agent_trials import BASE, prepare, tasks, verify
from retrieval import load_documents, tokens

TOOL_DEFINITIONS = [
    {"name": "retrieve", "description": "Find navigation hints for a query; follow with read. Requested cl100k text budget may be approximate for the backend.",
     "parameters": {"query": "nonempty string", "budget": "integer 1..8000, default 2000"}},
    {"name": "read", "description": "Read repository source lines; paths in retrieval output are accepted. Source read fallback is identical for both backends.",
     "parameters": {"path": "repository-relative path or cited index path", "start": "positive line, default 1", "lines": "1..200, default 80", "budget": "integer 1..8000, default 2000"}},
    {"name": "search", "description": "Fallback regex search over eligible repository code and docs using ripgrep; returns bounded matching lines.",
     "parameters": {"pattern": "regex string up to 256 characters", "budget": "integer 1..8000, default 2000"}},
    {"name": "edit", "description": "Replace exactly one matching source string. Test/harness files cannot be edited. Indexes refresh on the next retrieve call.",
     "parameters": {"path": "repository-relative source path", "old": "unique nonempty string", "new": "replacement string"}},
    {"name": "outline", "description": "List a source file's functions with line ranges, so you can read one function instead of the whole file.",
     "parameters": {"path": "repository-relative source path (.go, .py, .js)"}},
    {"name": "test", "description": "Run the trusted behavioral verification command; nonzero exit is a failure.", "parameters": {}},
    {"name": "finish", "description": "End the attempt and independently run trusted verification again.", "parameters": {}},
]


GRAF_QUERY_FLAGS = {"graf": (), "graf_floor": ("--file-score", "damped", "--stop", "floor")}
ORIENT_ARMS = ("graf_orient", "graphify_orient")
ARMS = (*GRAF_QUERY_FLAGS, "graphify", "search_only", "ast_graph", *ORIENT_ARMS)
SCRIPTS = Path(__file__).resolve().parent
ORIENT_BUDGET = 1500  # Batch t5: the orientation shown before the first turn (preregistration v22).
GRAF_INDEX_ARMS = (*GRAF_QUERY_FLAGS, "graf_orient")


def tool_definitions(arm):
    """The operations an arm's model can request; search-only has no retriever, and only graf_orient outlines."""
    return [d for d in TOOL_DEFINITIONS
            if not (arm == "search_only" and d["name"] == "retrieve")
            and not (arm != "graf_orient" and d["name"] == "outline")]


def retrieve_command(arm, input_dir, output_dir, query, budget):
    if arm in GRAF_QUERY_FLAGS:
        return [str(BASE / "target/release/graf"), "--root", str(input_dir), "locate",
                "--budget", str(budget), "--limit", "1000", "--text", *GRAF_QUERY_FLAGS[arm], "--", query]
    if arm == "ast_graph":
        return [sys.executable, str(SCRIPTS / "ast_retrieve.py"), "query", str(output_dir), query, str(budget)]
    if arm == "graf_orient":
        return [str(BASE / "target/release/graf"), "--root", str(input_dir), "units", "--budget", str(budget),
                "--text", "--", query]
    if arm in ("graphify", "graphify_orient"):
        return [sys.executable, str(BASE / "benchmark/compare_graphify.py"), "--graphify-query",
                str(output_dir / "graphify-out/graph.json"), query, str(budget)]
    raise ValueError("retrieve is not available in this arm")


class InfrastructureError(RuntimeError):
    """A broker tool failed for reasons outside the agent's control; the trial is invalid."""


def run_tool(command, check=True, **kwargs):
    """Run a broker backend; a missing or failing executable is infrastructure, not the agent."""
    try:
        return subprocess.run(command, capture_output=True, text=True, check=check, **kwargs)
    except (OSError, subprocess.SubprocessError) as error:
        raise InfrastructureError(f"{Path(command[0]).name} failed: {error}") from error


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def bounded(text, budget):
    """Keep complete lines. The diagnostic count is not provider usage."""
    result = ""
    for line in text.splitlines(keepends=True):
        candidate = result + line
        if len(tokens(candidate)) > budget:
            break
        result = candidate
    return result


def budget_of(args):
    budget = args.get("budget", 2000)
    if type(budget) is not int or not 1 <= budget <= 8000:
        raise ValueError("budget must be an integer from 1 to 8000")
    return budget


def is_test_path(path):
    p = Path(path)
    return (any(x in {"test", "tests", "__tests__"} for x in p.parts)
            or p.name.startswith("test_") or p.name.endswith("_test.go")
            or ".test." in p.name or ".spec." in p.name or p.name == "conftest.py")


def graphify_pin():
    root = BASE / ".research/corpora/graphify"
    package = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    if importlib.metadata.version(package["name"]) != package["version"]:
        raise ValueError("Install the pinned Graphify package before starting trials")
    import graphify
    installed = Path(graphify.__file__).parent
    for path in installed.rglob("*.py"):
        original = root / "graphify" / path.relative_to(installed)
        if not original.is_file() or path.read_bytes() != original.read_bytes():
            raise ValueError("Installed Graphify source differs from the pinned snapshot")
    manifest = json.loads((BASE / "benchmark/corpora.json").read_text())
    return next(x for x in manifest["repositories"] if x["id"] == "graphify")


class Session:
    def __init__(self, task, trial_id, arm):
        if arm not in ARMS:
            raise ValueError(f"arm must be one of {', '.join(ARMS)}")
        self.task, self.arm = task, arm
        self.root = (BASE / ".research/trials" / trial_id / "workspace").resolve()
        self.directory = self.root.parent
        if not self.root.is_relative_to((BASE / ".research/trials").resolve()):
            raise ValueError("Unsafe trial id")
        self.input = self.directory / "retrieval-input"
        self.output = self.directory / "retrieval-output"
        self.dirty = True
        self.closed = False
        self.tool_count = 0
        self.build_seconds = 0.0
        self.infrastructure_failed = False
        self.events = self.directory / "tool-events.jsonl"
        self.protected = {}
        if not self.root.is_dir():
            raise ValueError("Prepare the trial before opening a session")
        if json.loads((self.directory / "task.json").read_text())["task"] != task["id"]:
            raise ValueError("Workspace/task identity mismatch")
        metadata = self.directory / "session.json"
        if metadata.exists():
            saved = json.loads(metadata.read_text())
            if saved["arm"] != arm or saved["task"] != task["id"]:
                raise ValueError("Session arm/task cannot change between calls")
            self.protected = saved["protected_source_hashes"]
        else:
            for path, text in load_documents(self.root).items():
                if is_test_path(path):
                    self.protected[path] = sha(text)
        state = self.directory / "tool-state.json"
        if state.exists():
            saved = json.loads(state.read_text())
            self.closed, self.tool_count = saved["closed"], saved["tool_count"]
            self.build_seconds = saved["build_seconds"]
            self.infrastructure_failed = saved.get("infrastructure_failed", False)

    def path(self, name):
        if not isinstance(name, str) or not name:
            raise ValueError("A source path is required")
        candidate = Path(name)
        if candidate.is_absolute():
            # Graphify cites its staged input, so map those hints back to the faulty workspace.
            if candidate.is_relative_to(self.input):
                candidate = candidate.relative_to(self.input)
            elif candidate.is_relative_to(self.root):
                candidate = candidate.relative_to(self.root)
            else:
                raise ValueError("Path is outside the trial workspace")
        if not candidate.parts or any(p in {"..", ".graf", "node_modules", ".git"} or p.startswith(".") for p in candidate.parts):
            raise ValueError("Hidden, parent, generated-index, and dependency paths are unavailable")
        full = self.root / candidate
        cursor = self.root
        for part in candidate.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("Symlink source paths are unavailable")
        if not full.resolve().is_relative_to(self.root) or not full.is_file():
            raise ValueError("Source file is outside the workspace or missing")
        if full.stat().st_size > 1_000_000:
            raise ValueError("Source file exceeds the shared 1 MB read limit")
        return full

    def rebuild(self):
        start = time.perf_counter()
        files = load_documents(self.root)
        for directory in (self.input, self.output):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir()
        for name, text in files.items():
            path = self.input / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if self.arm in GRAF_INDEX_ARMS:
            command = [str(BASE / "target/release/graf"), "--root", str(self.input), "index", "--rebuild"]
        elif self.arm == "ast_graph":
            command = [sys.executable, str(SCRIPTS / "ast_retrieve.py"), "index", str(self.input), str(self.output)]
        else:
            graphify_pin()
            command = [str(BASE / ".research/venv/bin/graphify"), "extract", str(self.input),
                       "--code-only", "--no-cluster", "--max-workers", "2", "--out", str(self.output)]
        result = run_tool(command, timeout=180, env=self.environment())
        seconds = time.perf_counter() - start
        self.build_seconds += seconds
        generation = sum(1 for _ in self.directory.glob("index-build-*.json")) + 1
        (self.directory / f"index-build-{generation}.json").write_text(json.dumps({
            "arm": self.arm, "command": command, "seconds": seconds,
            "source_hashes": {p: sha(t) for p,t in files.items()},
            "stdout": result.stdout, "stderr": result.stderr,
            "snapshot": sha(json.dumps(files, sort_keys=True)),
        }, indent=2) + "\n")
        if self.arm in (*GRAF_INDEX_ARMS, "ast_graph"):
            built = self.input / ".graf/index.json" if self.arm in GRAF_INDEX_ARMS else self.output / "ast-index.json"
            index = json.loads(built.read_text())
            if set(index["files"]) != set(files):
                raise RuntimeError("graf source filters differ from the shared input set")
        self.dirty = False

    @staticmethod
    def environment():
        import os
        return {**os.environ, "PYTHONHASHSEED": "0"}

    def verification(self):
        # Prevent direct edits to visible verification fixtures from becoming a passing trial.
        for name, hashed in self.protected.items():
            protected = self.root / name
            if not protected.is_file() or sha(protected.read_text()) != hashed:
                return {"verification_passed": False, "reason": "Protected tests changed"}
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            verify(self.task, self.root)
        result = json.loads(stream.getvalue())
        log = self.directory / f"verification-{self.tool_count}.json"
        log.write_text(json.dumps(result, indent=2) + "\n")
        return {"verification_passed": result["verification_passed"], "returncode": result["returncode"],
                "output": bounded(result["stdout"] + result["stderr"], 2000)}

    def operate(self, operation, args):
        if self.closed:
            raise ValueError("Trial is already finished")
        if operation == "retrieve":
            budget = budget_of(args)
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query must be nonempty")
            command = retrieve_command(self.arm, self.input, self.output, query, budget)
            if self.dirty:
                self.rebuild()
            context = run_tool(command, timeout=90, env=self.environment()).stdout
            return {"content": context, "requested_budget": budget, "reference_text_tokens": len(tokens(context)),
                    "over_reference_budget": len(tokens(context)) > budget}
        if operation == "orient":
            if self.arm not in ORIENT_ARMS or self.tool_count != 1:
                raise ValueError("Orientation is produced once, by the harness, before the first turn")
            if self.dirty:
                self.rebuild()
            if self.arm == "graf_orient":
                command = [str(BASE / "target/release/graf"), "--root", str(self.input), "triage", "--budget",
                           str(ORIENT_BUDGET), "--text", "--", self.task["issue"]]
            else:
                command = retrieve_command(self.arm, self.input, self.output, self.task["issue"], ORIENT_BUDGET)
            native = run_tool(command, timeout=90, env=self.environment()).stdout
            context = bounded(native, ORIENT_BUDGET)  # The same hard cap for both backends.
            return {"content": context, "requested_budget": ORIENT_BUDGET, "reference_text_tokens": len(tokens(context)),
                    "native_tokens": len(tokens(native)), "truncated": context != native}
        if operation == "outline":
            if self.arm != "graf_orient":
                raise ValueError("outline is not available in this arm")
            relative = str(self.path(args.get("path")).relative_to(self.root))
            if self.dirty:
                self.rebuild()
            command = [str(BASE / "target/release/graf"), "--root", str(self.input), "outline", "--text", "--", relative]
            result = run_tool(command, check=False, timeout=60, env=self.environment())
            if result.returncode:
                raise ValueError(result.stderr.strip() or "outline failed")
            return {"content": result.stdout}
        if operation == "read":
            path = self.path(args.get("path"))
            start, count = args.get("start", 1), args.get("lines", 80)
            if type(start) is not int or start < 1 or type(count) is not int or not 1 <= count <= 200:
                raise ValueError("start must be positive; lines must be 1..200")
            text = path.read_text()
            lines = text.splitlines()
            content = "\n".join(f"L{i+1}: {lines[i]}" for i in range(start-1, min(start-1+count, len(lines))))
            delivered = bounded(content, budget_of(args))
            return {"path": str(path.relative_to(self.root)), "source_hash": sha(text),
                    "requested_start": start, "file_lines": len(lines),
                    "content": delivered, "truncated": delivered != content}
        if operation == "search":
            pattern = args.get("pattern")
            if not isinstance(pattern, str) or not pattern or len(pattern) > 256:
                raise ValueError("pattern must have 1..256 characters")
            files = sorted(load_documents(self.root))
            if not files:
                return {"content": ""}
            command = ["rg", "--no-config", "--color", "never", "--line-number", "--max-count", "10", "--", pattern, *files]
            result = run_tool(command, check=False, cwd=self.root, timeout=30)
            if result.returncode not in (0, 1):
                raise ValueError(result.stderr.strip())
            delivered = bounded(result.stdout, budget_of(args))
            return {"content": delivered, "truncated": delivered != result.stdout}
        if operation == "edit":
            path = self.path(args.get("path"))
            relative = str(path.relative_to(self.root))
            if is_test_path(relative) or path.name in {"package.json", "pyproject.toml", "go.mod", "Makefile"}:
                raise ValueError("Test and build configuration changes are outside these repair fixtures")
            old, new = args.get("old"), args.get("new")
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ValueError("old/new must be strings; old must be nonempty")
            text = path.read_text()
            if text.count(old) != 1:
                raise ValueError("old must match exactly one occurrence")
            path.write_text(text.replace(old, new, 1))
            self.dirty = True
            return {"path": relative, "source_hash": sha(path.read_text()), "edited": True}
        if operation in {"test", "finish"}:
            try:
                result = self.verification()
                if operation == "finish":
                    result["finished"] = True
                return result
            finally:
                if operation == "finish":
                    self.closed = True
        raise ValueError("Unknown tool operation")

    def call(self, operation, args):
        already_closed = self.closed
        self.tool_count += 1
        start = time.perf_counter()
        try:
            value = self.operate(operation, args)
            response = {"ok": True, "result": value}
        except InfrastructureError as error:
            self.infrastructure_failed = True
            response = {"ok": False, "error": str(error), "infrastructure": True}
        except Exception as error:
            response = {"ok": False, "error": str(error)}
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        event = {"type": "tool", "operation": operation, "arguments": args, "response": response,
                 "wall_s": time.perf_counter()-start, "reference_payload_tokens": len(tokens(payload)),
                 "provider_usage": "unmeasured; solver adapter must record all model calls"}
        with self.events.open("a") as stream:
            stream.write(json.dumps(event) + "\n")
            if operation == "finish" and self.closed and not already_closed:  # Only the closing call grades.
                stream.write(json.dumps({"type": "verification",
                    "passed": response.get("result", {}).get("verification_passed") is True,
                    "source": "trusted verifier; not a solver claim"}) + "\n")
        (self.directory / "tool-state.json").write_text(json.dumps({
            "closed": self.closed, "tool_count": self.tool_count, "build_seconds": self.build_seconds,
            "infrastructure_failed": self.infrastructure_failed}) + "\n")
        return response


def prepare_session(task, trial_id, arm):
    with contextlib.redirect_stdout(io.StringIO()):
        prepare(task, trial_id)
    session = Session(task, trial_id, arm)
    (session.directory / "session.json").write_text(json.dumps({
        "task": task["id"], "arm": arm, "issue": task["issue"],
        "tools": tool_definitions(arm), "solver_status": "not_configured",
        "protected_source_hashes": session.protected,
        "fault_metadata_exposed_to_model": False,
    }, indent=2) + "\n")
    return session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "serve"])
    parser.add_argument("task", choices=[t["id"] for t in tasks()])
    parser.add_argument("trial_id")
    parser.add_argument("--arm", required=True, choices=ARMS)
    args = parser.parse_args()
    task = next(t for t in tasks() if t["id"] == args.task)
    session = prepare_session(task, args.trial_id, args.arm) if args.action == "prepare" else Session(task, args.trial_id, args.arm)
    if args.action == "prepare":
        print(json.dumps({"issue": task["issue"], "tools": tool_definitions(args.arm),
                          "session": str(session.directory / "session.json")}))
        return
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = session.call(request["operation"], request.get("arguments", {}))
        except Exception as error:
            response = {"ok": False, "error": str(error)}
        print(json.dumps(response, ensure_ascii=False), flush=True)
        if session.closed:
            break


if __name__ == "__main__":
    main()
