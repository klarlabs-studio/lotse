"""Audit the current paired comparison against source receipts and raw outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

from compare_graphify import ARMS, GRAF_ARMS, INDEX_VARIANTS, variant_name, variant_root, cited_paths, cited_path_order, code_hint_recall, validate_locate, graf_query_command, graphify_command
from retrieval import BASE, graf_artifact, graf_index, graphify_baselines, tokens
from compare_graphify import validate_focused_units


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-search-unchanged", action="store_true",
                        help="For this delivery-only experiment, also compare search outputs/indexes to the saved v1 run")
    parser.add_argument("--comparison", type=Path, default=BASE / "results/graphify-comparison.json")
    args = parser.parse_args()
    source = args.comparison.resolve()
    result = json.loads(source.read_text())
    arms, variants = ARMS, INDEX_VARIANTS
    binary = BASE / result.get("binary_path", "target/release/graf")
    if "run_plan" in result:
        from comparison_plan import load_plan, selected_layout
        plan_path = BASE / result["run_plan"]["path"]
        assert sha(plan_path) == result["run_plan"]["sha256"]
        prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
        plan, inputs = load_plan(plan_path, prereg, BASE, GRAF_ARMS)
        arms = tuple(plan["arms"])
        _, variants = selected_layout(arms, GRAF_ARMS)
        assert result["arm_binaries"] == plan["binaries"]
        assert result["binary_path"] == plan["index_binary"]["path"]
        assert result["queries"] == plan["queries"]["path"]
        assert result["input_receipts"] == inputs["snapshots"]
        assert result["baseline"]["pin"] in graphify_baselines(inputs["corpora"])
        assert result["repeats"] == plan["repeats"]
        assert source == BASE / plan["output"]
        assert set(result["repositories"]) == {r["id"] for r in inputs["corpora"]["repositories"] if r.get("role") != "baseline"}
    assert set(result["arms"]) == set(arms)
    queries = BASE / result.get("queries", "benchmark/queries.json")
    scratch = BASE / result.get("scratch", ".research/comparison")
    assert sha(BASE / "benchmark/compare_graphify.py") == result["driver_sha256"]
    assert sha(queries) == result["query_sha256"]
    assert sha(binary) == result["binary_sha256"]
    for path, hashed in result["source_hashes"].items():
        assert sha(BASE / path) == hashed, f"Stale source receipt: {path}"
    cases = {q["id"]: q for q in json.loads(queries.read_text())}
    expected = {(q, budget) for q in cases for budget in (2000, 8000)}
    assert len(result["rows"]) == len(expected)
    indexes = {}
    for repo, info in result["repositories"].items():
        root = scratch / repo / "input"
        index_path = graf_artifact(root)
        index = graf_index(binary, root)
        assert set(index["files"]) == set(info["file_hashes"])
        assert sha(index_path) == info["builds"]["graf"]["artifact_sha256"]
        indexes[repo, ()] = index
        for flags in variants[1:]:
            variant = graf_artifact(variant_root(root.parent, flags))
            indexes[repo, flags] = graf_index(binary, variant.parents[1])
            assert set(indexes[repo, flags]["files"]) == set(info["file_hashes"])
            assert sha(variant) == info["builds"][variant_name(flags)]["artifact_sha256"]
        graph_path = root.parent / "graph/graphify-out/graph.json"
        assert sha(graph_path) == info["builds"]["graphify"]["artifact_sha256"]
        for name, hashed in info["file_hashes"].items():
            assert sha(root / name) == hashed
        if "run_plan" in result and "source_manifest" in inputs:
            assert info["file_hashes"] == inputs["source_manifest"][repo]
    seen = set()
    timing_samples = 0
    hints = 0
    for row in result["rows"]:
        pair = row["id"], row["budget"]
        assert pair not in seen
        seen.add(pair)
        assert row["repo"] == cases[row["id"]]["repo"]
        assert set(row["tools"]) == set(arms)
        root = scratch / row["repo"] / "input"
        files = result["repositories"][row["repo"]]["file_hashes"]
        for tool, value in row["tools"].items():
            if "run_plan" in result:
                if tool == "graphify":
                    command = graphify_command(root.parent / "graph/graphify-out/graph.json",
                                               cases[row["id"]]["query"], row["budget"], plan["graphify_adapter"])
                else:
                    flags = GRAF_ARMS[tool][0]
                    command = graf_query_command(BASE / plan["binaries"][tool]["path"],
                                                 variant_root(root.parent, flags), tool,
                                                 cases[row["id"]]["query"], row["budget"])
                assert value["command"] == command
            samples = value["latency_ms"]
            assert len(samples) == result["repeats"] and all(t >= 0 for t in samples)
            timing_samples += len(samples)
            assert statistics.median(samples) == value["median_ms"]
            context = value["context"]
            count = len(tokens(context))
            assert count == value["output_tokens"]
            assert (count > row["budget"]) == value["over_cl100k_budget"]
            if tool.startswith("graf"):
                assert count <= row["budget"]
            paths = cited_paths(tool, context, root, files)
            ordered = cited_path_order(tool, context, root, files)
            assert paths == value["cited_paths"]
            assert ordered == value["cited_path_order"]
            assert len(paths) == value["cited_file_count"]
            assert code_hint_recall(cases[row["id"]], paths) == value["code_file_hint_recall"]
            for limit in (5, 10):
                assert code_hint_recall(cases[row["id"]], ordered[:limit]) == value[f"code_file_hint_recall_at_{limit}"]
            if tool.startswith("graf_locate"):
                flags = GRAF_ARMS[tool][0]
                verified = validate_locate(context, variant_root(root.parent, flags), indexes[row["repo"], flags])
                assert verified == value["validated_hint_count"]
                hints += verified
            if tool in ("graf_units_focus", "graf_units_symbols", "graf_units_retain_body", "graf_units_diverse", "graf_units_linked", "graf_units_tail"):
                assert validate_focused_units(context, root) == value["validated_source_units"]
            if tool in ("graf_evidence", "graf_evidence_units", "graf_evidence_linked", "graf_evidence_bundles", "graf_evidence_coverage", "graf_evidence_families", "graf_evidence_passages", "graf_evidence_complement", "graf_evidence_stratified", "graf_evidence_fused", "graf_evidence_referenced", "graf_evidence_rescue", "graf_evidence_callees", "graf_evidence_selective"):
                assert validate_focused_units(context, root, physical=True) == value["validated_source_units"]
            if "runs" in value:
                assert len(value["runs"]) == result["repeats"]
                for i, run in enumerate(value["runs"]):
                    assert run["repeat"] == i and run["stdout"] == context
                    assert run["elapsed_ms"] == samples[i]
                    assert run["returncode"] == 0 or (
                        tool == "graphify" and result["baseline"]["adapter"] == "native-cli-v2"
                        and run["returncode"] == 4 and context == "No matching nodes found.\n")
    assert seen == expected
    for budget in (2000, 8000):
        for tool in arms:
            values = [r["tools"][tool] for r in result["rows"] if r["budget"] == budget]
            summary = result["summary"][str(budget)][tool]
            for metric in ("code_file_hint_recall", "code_file_hint_recall_at_5", "code_file_hint_recall_at_10"):
                assert statistics.mean(v[metric] for v in values if v[metric] is not None) == summary[metric]
            assert statistics.median(v["median_ms"] for v in values) == summary["median_query_ms"]
            assert statistics.median(v["cited_file_count"] for v in values) == summary["median_cited_files"]
            assert statistics.median(v["output_tokens"] for v in values) == summary["median_output_tokens"]
            assert sum(v["over_cl100k_budget"] for v in values) == summary["over_cl100k_budget"]
    if args.expect_search_unchanged:
        before = json.loads((BASE / "results/graphify-comparison-search-v1.json").read_text())
        original = {(r["id"], r["budget"]): r for r in before["rows"]}
        for row in result["rows"]:
            assert row["tools"]["graf"]["context"] == original[row["id"], row["budget"]]["tools"]["graf"]["context"]
        for repo, info in result["repositories"].items():
            assert info["builds"]["graf"]["artifact_sha256"] == before["repositories"][repo]["builds"]["graf"]["artifact_sha256"]
    audit = {"status": "passed", "comparison_sha256": sha(source), "cases": len(seen),
             "timing_samples": timing_samples, "verified_locate_hints": hints,
             "search_outputs_and_indexes_unchanged_from_v1": args.expect_search_unchanged}
    source.with_name(source.stem + "-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
