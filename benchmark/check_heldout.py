"""Validate held-out query drafts before they are frozen.

    python benchmark/check_heldout.py FILE.json [--repo cobra]

Checks the schema, per-repository category quotas, unique IDs, that every
evidence path is an indexed file of the pinned snapshot, that anchors occur on
their cited lines, that file hashes match, that answerable questions cite code,
and that no question repeats a development query.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from retrieval import BASE, load_documents

REPOS = ("cobra", "flask", "fastify")
QUOTA = {"exact": 8, "conceptual": 8, "trace": 8, "architecture": 4, "impact": 5,
         "discrepancy": 4, "no_evidence": 4}
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
STATUS = "drafted_without_retrieval_tools_not_independently_reviewed"


def check(cases, repos):
    errors = []
    documents = {repo: load_documents(BASE / ".research/corpora" / repo) for repo in repos}
    development = {q["query"].strip().lower() for q in
                   json.loads((BASE / "benchmark/queries.json").read_text())}
    ids = Counter(c.get("id") for c in cases)
    errors += [f"Duplicate id {i}" for i, n in ids.items() if n > 1]
    for repo in repos:
        counts = Counter(c.get("category") for c in cases if c.get("repo") == repo)
        if dict(counts) != QUOTA:
            errors.append(f"{repo}: categories {dict(counts)} differ from quota {QUOTA}")
    for case in cases:
        errors += [f"{case.get('id')}: {e}" for e in check_case(case, documents, development)]
    return errors


def check_case(case, documents, development):
    errors = []
    if set(case) != {"id", "repo", "category", "query", "evidence", "expected", "annotation_status"}:
        return [f"fields {sorted(case)}"]
    if case["repo"] not in documents or not case["id"].startswith(f"h-{case['repo']}-"):
        return ["unknown repo or id prefix"]
    if case["annotation_status"] != STATUS:
        errors.append("annotation_status")
    if case["query"].strip().lower() in development:
        errors.append("repeats a development query")
    negative = case["category"] == "no_evidence"
    expected = "insufficient_repository_evidence" if negative else "retrieve_evidence"
    if case["expected"] != expected or negative != (not case["evidence"]):
        errors.append("expected/evidence inconsistent with category")
    files = documents[case["repo"]]
    for item in case["evidence"]:
        path = item.get("path")
        if path not in files:
            errors.append(f"{path} is not an indexed file")
            continue
        text = files[path]
        lines = text.splitlines()
        start, end = item.get("start"), item.get("end")
        if not (isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= len(lines)):
            errors.append(f"{path}: bad range {start}-{end}")
            continue
        if not item.get("anchor") or item["anchor"] not in lines[start - 1]:
            errors.append(f"{path}:{start}: anchor not on line")
        if item.get("file_sha256") != hashlib.sha256(text.encode()).hexdigest():
            errors.append(f"{path}: file_sha256 mismatch")
    if not negative and not any(Path(e["path"]).suffix not in DOC_SUFFIXES for e in case["evidence"]):
        errors.append("answerable question without code evidence")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--repo", choices=REPOS, action="append")
    args = parser.parse_args()
    cases = json.loads(args.file.read_text())
    errors = check(cases, args.repo or REPOS)
    for error in errors:
        print(error)
    print(f"{len(cases)} cases, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
