"""Sequential diagnostic of private-snapshot and broker setup overhead.

This measures component wall times and native child time for frozen retrieval
rows. It is a local diagnostic only; it makes no performance or promotion claim.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from agent_backend import QUERY_ARGS
from probe_claude_subscription_v2 import sha
from probe_readonly_broker import request, tokenizer_identity
from readonly_broker_v2 import Broker, source_manifest_sha256
from readonly_broker_stdio_v4 import index_options, staged_plan
from readonly_snapshot_v2 import Snapshot
from retrieval import tokens

BASE = Path(__file__).resolve().parents[1]
ARMS = {"graf_evidence_selective": "graf_v61", "graf_evidence_rescue": "graf_v59", "graphify": "graphify"}
PERMUTATIONS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


def counterbalanced_arms(block_index: int) -> list[str]:
    arms = list(ARMS)
    return [arms[i] for i in PERMUTATIONS[block_index % len(PERMUTATIONS)]]


def finalize_metrics(record: dict, actual_wall_ms: float) -> None:
    phases = record["phases"]
    record["actual_wall_ms"] = actual_wall_ms
    record["native_child_ms"] = sum(record["native_child_samples_ms"])
    record["total_ms"] = sum(value for value in phases.values() if value is not None)
    record["overhead_total_minus_native_ms"] = record["total_ms"] - record["native_child_ms"]


def serialized_payload_tokens(*values) -> int:
    return sum(len(tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":"))))
               for value in values if value is not None)


def session_passed(record: dict) -> bool:
    return (not record["errors"] and record.get("retrieve_success") is True and
            all(record.get("payload_parity", {}).values()) and
            all(record.get("source_parity", {}).values()) and
            record.get("snapshot_cleanup", {}).get("removed") is True)


def save_exclusive(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def expected_stdout(native: str, arm: str, old_graph: Path, new_graph: Path) -> str:
    if arm != "graphify":
        return native
    prefix = "Graph: " + old_graph.relative_to(BASE).as_posix() + " "
    if not native.startswith(prefix):
        raise ValueError("unexpected_graph_header")
    return "Graph: " + new_graph.relative_to(BASE).as_posix() + " " + native[len(prefix):]


def make_spec(plan, old_plan, comparison, repo, arm, root, graph):
    info = comparison["repositories"][repo]
    barm = ARMS[arm]
    binary_pin = plan["binary_pins"][arm]
    binary = Path(binary_pin["path"])
    binary = binary if binary.is_absolute() else BASE / binary
    if sha(binary) != binary_pin["sha256"]:
        raise ValueError("binary_pin_mismatch:" + arm)
    artifact = graph if arm == "graphify" else root / ".graf/index.bin"
    digest = info["builds"]["graphify" if arm == "graphify" else "graf"]["artifact_sha256"]
    manifest = info["file_hashes"]
    return {"comparison": {"shared_output_budget": 8000}, "source_manifest": manifest,
            "arms": [{"id": barm, "binary": str(binary), "binary_sha256": binary_pin["sha256"],
                      "query_args": QUERY_ARGS[barm]}],
            "artifacts": {barm: {"path": str(artifact), "sha256": digest,
                                  "source_manifest_sha256": source_manifest_sha256(manifest)}},
            "subprocess_environment": {"PYTHONHASHSEED": "0"}, "max_actions": 2,
            "sources": old_plan["sources"],
            "snapshot_index_build": dict(old_plan["index_build"], semantics_sha256=old_plan["index_semantics"][repo])}


def _native_runner(samples):
    def run(*args, **kwargs):
        start = time.perf_counter()
        try:
            return subprocess.run(*args, **kwargs)
        finally:
            samples.append((time.perf_counter() - start) * 1000)
    return run


def run_session(plan, old_plan, evidence, comparison, cases, row, repo, arm, rep, position):
    barm = ARMS[arm]
    info = comparison["repositories"][repo]
    origin = BASE / comparison["scratch"] / repo / "input"
    original_graph = origin.parent / "graph/graphify-out/graph.json"
    manifest = info["file_hashes"]
    rel = sorted(manifest)[0]
    query = cases[row["id"]]["query"]
    stem = f"r{rep:02d}-p{position:02d}-{row['id']}-{row['budget']}-{barm}"
    folder = evidence / stem
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    timing = {key: None for key in ("snapshot_enter_wall_ms", "broker_init_wall_ms", "retrieve_wall_ms",
                                      "source_read_wall_ms", "broker_close_wall_ms", "snapshot_cleanup_wall_ms")}
    native_samples = []
    record = {"session": stem, "repo": repo, "query_id": row["id"], "budget": row["budget"],
              "arm": barm, "repetition": rep, "counterbalance_position": position,
              "phases": timing, "native_child_samples_ms": native_samples,
              "errors": [], "payload_parity": None}
    snapshot = broker = None
    snapshot_root = None
    retrieval = reading = retrieve_request = read_request = None
    t = time.perf_counter()
    try:
        mark = time.perf_counter()
        try:
            spec = make_spec(plan, old_plan, comparison, repo, arm, origin, original_graph)
            pin = spec["artifacts"][barm]
            snapshot = Snapshot(origin, manifest, Path(pin["path"]), pin["sha256"], barm, evidence,
                                **index_options(spec, barm))
            snapshot.__enter__()
            snapshot_root = str(snapshot.root)
        finally:
            timing["snapshot_enter_wall_ms"] = (time.perf_counter() - mark) * 1000
        if snapshot.receipt is not None:
            save_exclusive(folder / "snapshot-receipt.json", snapshot.receipt)

        local = staged_plan(spec, barm, snapshot)
        mark = time.perf_counter()
        try:
            broker = Broker(local, barm, snapshot.root, snapshot.graph_path, folder / "journal.jsonl",
                            max_actions=2, runner=_native_runner(native_samples))
        finally:
            timing["broker_init_wall_ms"] = (time.perf_counter() - mark) * 1000

        retrieve_request = request(1, "retrieve", {"query": query, "budget": row["budget"]})
        mark = time.perf_counter()
        try:
            retrieval = broker.handle(retrieve_request)
        finally:
            timing["retrieve_wall_ms"] = (time.perf_counter() - mark) * 1000
        read_request = request(2, "read", {"path": rel, "start": 1, "lines": 2, "budget": 8000})
        mark = time.perf_counter()
        try:
            reading = broker.handle(read_request)
        finally:
            timing["source_read_wall_ms"] = (time.perf_counter() - mark) * 1000

        try:
            payload = json.loads(retrieval["result"]["content"][0]["text"])
            record["retrieve_success"] = retrieval.get("result", {}).get("isError") is False
            native = next(x for x in row["tools"][arm]["runs"] if x.get("stdout") is not None)
            expected = expected_stdout(native["stdout"], arm, original_graph, snapshot.graph_path)
            parity = {"stdout": payload.get("stdout") == expected,
                      "stderr": payload.get("stderr") == native["stderr"],
                      "returncode": payload.get("returncode") == native["returncode"],
                      "diagnostic_tokens": retrieval["result"].get("diagnostic_tokens") == len(tokens(payload.get("stdout", "")))}
            record["payload_parity"] = parity
            record["payload_tokens"] = len(tokens(payload.get("stdout", "")))
            if not all(parity.values()):
                record["errors"].append({"phase": "payload_parity", "error": "mismatch", "checks": parity})
        except Exception as exc:
            record["errors"].append({"phase": "payload_parity", "error_type": type(exc).__name__, "error": str(exc)})
        expected_read = "\n".join(f"{i}: {line}" for i, line in enumerate((origin / rel).read_text().splitlines()[:2], 1))
        record["source_parity"] = {"success": reading.get("result", {}).get("isError") is False,
                                   "text": reading.get("result", {}).get("source", {}).get("text") == expected_read,
                                   "sha256": reading.get("result", {}).get("source", {}).get("sha256") == manifest[rel]}
    except BaseException as exc:
        record["errors"].append({"phase": "session", "error_type": type(exc).__name__, "error": str(exc),
                                 "traceback": traceback.format_exc()})
    finally:
        if broker is not None:
            mark = time.perf_counter()
            try:
                broker.close()
            except BaseException as exc:
                record["errors"].append({"phase": "broker_close", "error_type": type(exc).__name__, "error": str(exc)})
            timing["broker_close_wall_ms"] = (time.perf_counter() - mark) * 1000
        if snapshot is not None:
            if snapshot_root is None:
                try:
                    snapshot_root = str(snapshot.root)
                except Exception:
                    pass
            if snapshot.receipt is not None and not (folder / "snapshot-receipt.json").exists():
                try:
                    save_exclusive(folder / "snapshot-receipt.json", snapshot.receipt)
                except Exception as exc:
                    record["errors"].append({"phase": "receipt_write", "error_type": type(exc).__name__, "error": str(exc)})
            mark = time.perf_counter()
            try:
                snapshot.__exit__(None, None, None)
            except BaseException as exc:
                record["errors"].append({"phase": "snapshot_cleanup", "error_type": type(exc).__name__, "error": str(exc)})
            timing["snapshot_cleanup_wall_ms"] = (time.perf_counter() - mark) * 1000
            record["snapshot_cleanup"] = {"root": snapshot_root,
                                          "removed": snapshot_root is not None and not Path(snapshot_root).exists()}
        finalize_metrics(record, (time.perf_counter() - t) * 1000)
        record["serialized_payload_tokens"] = serialized_payload_tokens(
            retrieve_request, retrieval, read_request, reading)
        record["ok"] = session_passed(record)
        raw = {"record": record, "retrieve_request": retrieve_request,
               "retrieve_reply": retrieval, "read_request": read_request, "read_reply": reading}
        try:
            save_exclusive(folder / "session.json", raw)
        except Exception as exc:
            record["errors"].append({"phase": "evidence_write", "error_type": type(exc).__name__, "error": str(exc)})
        record["ok"] = session_passed(record)
    return record


def run(plan_path: Path) -> int:
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text())
    for name, digest in plan["sources"].items():
        if sha(BASE / name) != digest:
            raise ValueError("source_pin_mismatch:" + name)
    if tokenizer_identity() != plan["tokenizer"]:
        raise ValueError("tokenizer_pin_mismatch")
    comparison_path = BASE / plan["comparison_output"]
    if sha(comparison_path) != plan["comparison_sha256"]:
        raise ValueError("comparison_pin_mismatch")
    comparison = json.loads(comparison_path.read_text())
    if sha(BASE / plan["comparison_plan"]) != comparison["run_plan"]["sha256"]:
        raise ValueError("comparison_plan_pin_mismatch")
    old_plan = json.loads((BASE / plan["old_snapshot_plan"]).read_text())
    if plan["repetitions"] != 3 or plan["budgets"] != [2000, 8000] or len(plan["query_ids"]) != 6:
        raise ValueError("unsupported_design")
    query_path = BASE / plan["queries"]
    if sha(query_path) != plan["queries_sha256"]:
        raise ValueError("query_file_pin_mismatch")
    cases = {q["id"]: q for q in json.loads(query_path.read_text())}
    if len(cases) != 6 or set(cases) != set(plan["query_ids"]):
        raise ValueError("query_ids_mismatch")
    by_repo = {}
    for case in cases.values():
        by_repo.setdefault(case["repo"], []).append(case["id"])
    expected_ids = {query_id for query_ids in by_repo.values() for query_id in sorted(query_ids)[:2]}
    if expected_ids != set(plan["query_ids"]):
        raise ValueError("queries_not_first_two_lex_per_repo")
    selected = [r for r in comparison["rows"] if r["id"] in plan["query_ids"] and r["budget"] in plan["budgets"]]
    if len(selected) != 12:
        raise ValueError("comparison_rows_incomplete")
    evidence = BASE / plan["evidence"]
    evidence.mkdir(parents=True, mode=0o700, exist_ok=False)
    rows = []
    blocks = [(rep, row) for rep in range(1, 4) for row in selected]
    for block_index, (rep, row) in enumerate(blocks):
        repo = row["repo"]
        if repo not in old_plan["index_semantics"]:
            raise ValueError("missing_old_snapshot_semantics:" + repo)
        arms = counterbalanced_arms(block_index)
        for position, arm in enumerate(arms):
            rows.append(run_session(plan, old_plan, evidence, comparison, cases, row, repo, arm, rep, position))
    report = {"scope": "snapshot and broker setup diagnostic; no model calls; no promotion claim",
              "status": "completed_with_errors" if any(not r["ok"] for r in rows) else "completed",
              "plan_sha256": sha(plan_path), "comparison_sha256": plan["comparison_sha256"],
              "sessions": rows, "session_count": len(rows), "model_calls": 0,
              "cache_conditions": "sequential within counterbalanced six-permutation blocks; OS caches uncontrolled",
              "timing_scope": "total_ms is the sum of the six named phase wall times; actual_wall_ms is the surrounding elapsed time and includes serialization, payload checks, plan setup and other gaps",
              "product_promotion_allowed": False, "agent_savings": "unmeasured"}
    save_exclusive(BASE / plan["output"], report)
    print(json.dumps({"status": report["status"], "sessions": len(rows)}))
    return int(report["status"] != "completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    sys.exit(run(parser.parse_args().plan))
