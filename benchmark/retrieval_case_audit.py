"""Recompute per-query retrieval facts from a saved Graphify comparison.

This is a descriptive development audit. It does not execute either retriever
and does not make a promotion or efficiency claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import compare_graphify as comparison

BASE = comparison.BASE
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
DECLARED_BUDGETS = (2000, 8000)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path, expected):
    value = json.loads(path.read_text())
    if not isinstance(value, expected):
        raise ValueError(f"{path} must contain {expected.__name__}")
    return value


def _doc_or_test(path):
    suffix = Path(path).suffix.lower()
    doc = suffix in DOC_SUFFIXES
    # Test classification is a path/name hint, not a claim about source purpose.
    test = bool(re.search(r"(?:^|[/_.-])(?:test|tests|spec|specs)(?:[/_.-]|$)", path, re.I))
    return {"documentation_heuristic": doc, "test_heuristic": test}


def _headers(arm, stdout, root, files):
    """Return citation sections, preserving repeats while keeping helper order."""
    order = comparison.cited_path_order(arm, stdout, root, files)
    if arm.startswith("graf"):
        sections = []
        for match in re.finditer(r'^("(?:[^"\\]|\\.)*"):(\d+)-(\d+)$', stdout, re.M):
            try:
                path = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise ValueError("malformed quoted graf citation header") from exc
            if path in files:
                sections.append({"path": path, "start": int(match.group(2)), "end": int(match.group(3))})
        return order, sections
    # Graphify's native renderer can have other NODE text; cited_path_order is
    # authoritative for paths, and each matching src header is one section.
    sections = []
    for match in re.finditer(r"\[src=(.*?) loc=(.*?) community=", stdout):
        path = match.group(1).removeprefix(str(root) + "/")
        if path in files:
            sections.append({"path": path, "location": match.group(2)})
    return order, sections


def audit(comparison_path, queries_path):
    comp = _load(comparison_path, dict)
    queries = _load(queries_path, list)
    query_hash = digest(queries_path)
    if comp.get("query_sha256") is not None and comp["query_sha256"] != query_hash:
        raise ValueError("comparison query_sha256 does not match frozen queries")
    if not isinstance(comp.get("scratch"), str) or not isinstance(comp.get("repositories"), dict):
        raise ValueError("comparison is missing scratch/repositories")
    arms_declared = comp.get("arms")
    repeats = comp.get("repeats")
    if not isinstance(arms_declared, dict) or not arms_declared:
        raise ValueError("comparison is missing declared arms")
    if not isinstance(repeats, int) or repeats < 1:
        raise ValueError("comparison has invalid repetition count")
    raw_scratch = BASE / comp["scratch"]
    for part in (raw_scratch, *raw_scratch.parents):
        if part == BASE.parent:
            break
        if part.is_symlink():
            raise ValueError(f"symlink in comparison scratch path: {part}")
    scratch = raw_scratch.resolve()
    try:
        scratch.relative_to(BASE.resolve())
    except ValueError as exc:
        raise ValueError("comparison scratch escapes repository base") from exc
    by_id = {}
    query_repos = set()
    for case in queries:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError("every frozen query must be an object with string id")
        if case["id"] in by_id:
            raise ValueError("duplicate frozen query id")
        if not isinstance(case.get("repo"), str):
            raise ValueError("frozen query is missing repository id")
        query_repos.add(case["repo"])
        by_id[case["id"]] = case
    if query_repos != set(comp["repositories"]):
        raise ValueError("frozen query repositories do not match comparison repositories")
    rows = comp.get("rows")
    if not isinstance(rows, list):
        raise ValueError("comparison rows must be a list")
    expected_rows = {(case_id, budget) for case_id in by_id for budget in DECLARED_BUDGETS}
    observed_rows = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in by_id or row.get("budget") not in DECLARED_BUDGETS:
            raise ValueError("comparison contains an unknown query or undeclared budget row")
        key = (row["id"], row["budget"])
        if key in observed_rows:
            raise ValueError("duplicate comparison query/budget row")
        observed_rows.add(key)
        if row.get("repo") != by_id[row["id"]]["repo"]:
            raise ValueError(f"comparison repository mismatch for query {row['id']}")
    if observed_rows != expected_rows:
        raise ValueError("comparison rows do not cover every query at every declared budget")
    results = []
    validated_manifests = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in by_id:
            raise ValueError("comparison row has no matching frozen query")
        case = by_id[row["id"]]
        repo = row.get("repo")
        budget = row.get("budget")
        if not isinstance(repo, str) or not isinstance(budget, int) or not isinstance(row.get("tools"), dict):
            raise ValueError("malformed comparison row")
        source = comp["repositories"].get(repo)
        if not isinstance(source, dict) or not isinstance(source.get("file_hashes"), dict):
            raise ValueError(f"missing frozen file hashes for {repo}")
        lexical_root = scratch / repo / "input"
        root = lexical_root.resolve()
        try:
            root.relative_to(scratch)
        except ValueError as exc:
            raise ValueError("repository input escapes scratch") from exc
        files = source["file_hashes"]
        if repo not in validated_manifests:
            if not files:
                raise ValueError(f"empty frozen source manifest for {repo}")
            for part in (lexical_root, *lexical_root.parents):
                if part == BASE.parent:
                    break
                if part.is_symlink():
                    raise ValueError(f"symlink in frozen source root: {part}")
            for rel, expected_hash in files.items():
                if not isinstance(rel, str) or Path(rel).is_absolute() or ".." in Path(rel).parts:
                    raise ValueError("unsafe path in frozen source manifest")
                path = lexical_root / rel
                cursor = lexical_root
                for component in Path(rel).parts:
                    cursor = cursor / component
                    if cursor.is_symlink():
                        raise ValueError(f"symlink in frozen source path: {repo}/{rel}")
                try:
                    path.resolve().relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"source path escapes frozen root: {repo}/{rel}") from exc
                if not path.is_file() or digest(path) != expected_hash:
                    raise ValueError(f"frozen source hash mismatch: {repo}/{rel}")
            validated_manifests[repo] = files
        for entry in case.get("evidence", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError(f"malformed expected evidence for {row['id']}")
            if entry["path"] not in files:
                raise ValueError(f"expected evidence missing from source manifest: {repo}/{entry['path']}")
        expected = sorted({e["path"] for e in case.get("evidence", [])
                           if isinstance(e, dict) and isinstance(e.get("path"), str)
                           and Path(e["path"]).suffix not in DOC_SUFFIXES})
        arms = {}
        if set(row["tools"]) != set(arms_declared):
            raise ValueError(f"comparison arm set mismatch: {row['id']}")
        for arm, data in row["tools"].items():
            if not isinstance(data, dict) or not isinstance(data.get("runs"), list) or not data["runs"]:
                raise ValueError(f"missing saved runs for {row['id']}/{arm}")
            if len(data["runs"]) != repeats:
                raise ValueError(f"repetition count mismatch: {row['id']}/{arm}")
            required = ("cited_path_order", "code_file_hint_recall", "code_file_hint_recall_at_5",
                        "code_file_hint_recall_at_10", "output_tokens")
            if any(field not in data for field in required):
                raise ValueError(f"missing required stored metric: {row['id']}/{arm}")
            per_run = []
            for run in data["runs"]:
                if not isinstance(run, dict) or not isinstance(run.get("stdout"), str):
                    raise ValueError(f"missing stdout in {row['id']}/{arm}")
                ordered, sections = _headers(arm, run["stdout"], root, files)
                recall = comparison.code_hint_recall(case, ordered)
                per_run.append((ordered, recall, sections))
            if any(r[0] != per_run[0][0] for r in per_run[1:]):
                raise ValueError(f"cited path order varies across repetitions: {row['id']}/{arm}")
            stored_order = data["cited_path_order"]
            if stored_order != per_run[0][0]:
                raise ValueError(f"stored cited_path_order mismatch: {row['id']}/{arm}")
            for field, limit in (("code_file_hint_recall_at_5", 5), ("code_file_hint_recall_at_10", 10)):
                calculated = comparison.code_hint_recall(case, per_run[0][0][:limit])
                if data[field] != calculated:
                    raise ValueError(f"stored {field} mismatch: {row['id']}/{arm}")
            if data["code_file_hint_recall"] != per_run[0][1]:
                raise ValueError(f"stored code_file_hint_recall mismatch: {row['id']}/{arm}")
            order, _, sections = per_run[0]
            ranks = {path: i + 1 for i, path in enumerate(order)}
            missing = [path for path in expected if path not in ranks]
            flagged_sections = [dict(path=s["path"], position=i + 1, **_doc_or_test(s["path"]))
                                for i, s in enumerate(sections)
                                if any(_doc_or_test(s["path"]).values())]
            tokens = data.get("output_tokens")
            if not isinstance(tokens, int) or tokens < 0:
                raise ValueError(f"invalid saved output_tokens: {row['id']}/{arm}")
            arms[arm] = {
                "cited_path_order": order, "citation_sections": sections,
                "repeated_path_section_count": len(sections) - len(order),
                "path_section_counts": {path: sum(s["path"] == path for s in sections) for path in order},
                "expected_code_files": expected,
                "expected_ranks": {path: ranks.get(path) for path in expected},
                "missing_anywhere": missing,
                "missing_top5": [p for p in expected if ranks.get(p, 10**9) > 5],
                "missing_top10": [p for p in expected if ranks.get(p, 10**9) > 10],
                "documentation_or_test_sections": flagged_sections,
                "recomputed_code_file_hint_recall": per_run[0][1],
                "recomputed_at5": comparison.code_hint_recall(case, order[:5]),
                "recomputed_at10": comparison.code_hint_recall(case, order[:10]),
                "stored_metrics": {k: data.get(k) for k in ("code_file_hint_recall", "code_file_hint_recall_at_5", "code_file_hint_recall_at_10")},
                "output_tokens_saved": tokens,
                "graphify_raw_distinct_path_count": len(set(p["path"] for p in sections)) if arm == "graphify" else None,
            }
        for arm, facts in arms.items():
            gt = next((a["output_tokens_saved"] for n, a in arms.items() if n == "graphify"), None)
            facts["flags"] = {
                "expected_after_top5": any(facts["expected_ranks"][p] is not None and facts["expected_ranks"][p] > 5 for p in facts["expected_code_files"]),
                "expected_absent": bool(facts["missing_anywhere"]),
                "docs_in_top5": any(_doc_or_test(path)["documentation_heuristic"]
                                     for path in facts["cited_path_order"][:5]),
                "output_tokens_exceeds_graphify": bool(arm != "graphify" and gt is not None and facts["output_tokens_saved"] > gt),
            }
        results.append({"query_id": row["id"], "repo": repo, "budget": budget, "arms": arms})
    source_hashes = {}
    for repo, source in comp["repositories"].items():
        for rel, sha in source.get("file_hashes", {}).items():
            source_hashes[f"{repo}/{rel}"] = sha
    return {
        "schema": "graf-retrieval-case-audit-v1",
        "purpose": "descriptive development evidence audit; no causal or promotion claim",
        "inputs": {"comparison": str(comparison_path), "comparison_sha256": digest(comparison_path),
                   "queries": str(queries_path), "queries_sha256": query_hash,
                   "source_hashes": source_hashes},
        "cases": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit(args.comparison.resolve(), args.queries.resolve())
        # Exclusive create: never overwrite an existing audit artifact.
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"retrieval case audit failed: {exc}", file=sys.stderr)
        return 2
    print(f"wrote descriptive case audit: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
