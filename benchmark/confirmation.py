"""Prepare a source-only confirmation packet and validate independent questions.

This does not run retrieval or grant benchmark acceptance. The historical
held-out workload and its decisions remain unchanged.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from retrieval import BASE, load_documents

DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
CASE_FIELDS = {"id", "repo", "category", "query", "evidence", "expected", "annotation_status"}
STATUS = "independent_source_only_pending_blind_pool_review"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(query):
    return " ".join(query.casefold().split())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def historical_exposure(root):
    """Conservative repository/question inventory, without reading retrieval outputs.

    Collect repository identities from prior input receipts and query strings
    from benchmark inputs. An absence here is local evidence, not proof that
    no person or model has ever seen a repository.
    """
    repositories, queries, receipts = set(), set(), {}

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("repo"), str):
                repositories.add(value["repo"])
            if isinstance(value.get("repository"), str):
                repositories.add(value["repository"].casefold().removesuffix(".git"))
                if isinstance(value.get("id"), str):
                    repositories.add(value["id"])
            if isinstance(value.get("query"), str):
                queries.add(normalized(value["query"]))
            for key, child in value.items():
                if key != "confirmation_preparation_v62":
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    paths = sorted(set((root / "benchmark").glob("*.json")) |
                   set((root / "results").glob("snapshots*.json")))
    for path in paths:
        # The prospective packet's own receipts are not historical exposure.
        if path.name in {"confirmation-v62.json", "snapshots-confirmation-v62.json"}:
            continue
        walk(json.loads(path.read_text()))
        receipts[path.relative_to(root).as_posix()] = sha(path)
    return {"repositories": sorted(repositories), "queries": sorted(queries),
            "input_sha256": receipts,
            "scope": "local benchmark JSON inputs and snapshot receipts; not a global independence guarantee"}


def author_instructions(spec):
    return f"""# Source-only question authoring

Use only this packet's sources, source-manifest.json and this document in a
fresh session. Do not inspect graf implementation, benchmark results, tuning
history, Graphify output or either retriever. Do not execute repository code.
The author must not be the agent/session that tuned the candidate.

Write questions.json as a JSON array of {sum(spec['category_quota'].values())} questions per
repository. Per-repository category quotas: {json.dumps(spec['category_quota'], sort_keys=True)}.
All categories except no_evidence must cite at least one code file. Use varied
natural questions: exact names, behavioral paraphrases, call traces, architecture,
change impact, and code/documentation discrepancies. Do not create repeated
paraphrases of a single task merely to fill quotas. Do not choose questions
based on expected retriever performance. Include implementation/test pairs
and multiple-file tasks where the question requires them.

Each object has exactly these fields:
id (h-REPO-unique-suffix), repo, category, query, evidence, expected,
annotation_status. Evidence is a list of objects containing path, start, end,
anchor, file_sha256. Paths are relative to sources/REPO. Lines start at 1;
anchor must occur on the start line. Hash the UTF-8 source file. Cite all
files needed to answer, not just files containing query words. For no_evidence,
evidence is [] and expected is insufficient_repository_evidence; explain the
source-search basis for absence in authoring-review.json. Other cases use
expected retrieve_evidence. Every annotation_status is {STATUS}.

Provide authoring-review.json with author_id, independent_of_tuning: true,
source_only: true, packet_sha256 (hash of packet.json), queries_sha256,
and no_evidence_rationales (map every negative-control ID to a substantive
explanation). These declarations record provenance; software cannot prove
the author's independence or the completeness of relevance labels.

A separate source reviewer must verify question answerability, anchors,
negative controls, relevance completeness and that tasks are distinct before
the question file is frozen in the active preregistration. After retrieval,
pool top-10 code files across all arms and obtain blind relevance judgments.
No comparison runs are authorized by the packet's preparation status alone.
"""


def exposure_receipt(exposure):
    # Preserve the inventory digest, not megabytes of historical issue text in
    # the preparation receipt or anywhere inside the source-only packet.
    return {k: v for k, v in exposure.items() if k != "queries"} | {
        "unique_historical_queries": len(exposure["queries"]),
        "normalized_queries_sha256": hashlib.sha256(
            json.dumps(exposure["queries"], ensure_ascii=True).encode()).hexdigest()}


def prepare(spec_path, output, root=BASE):
    spec = json.loads(spec_path.read_text())
    exposure = historical_exposure(root)
    repos = spec["repositories"]
    for repo in repos:
        if (repo["id"] in exposure["repositories"] or
                repo["repository"].casefold() in exposure["repositories"]):
            raise ValueError(f"Previously exposed repository: {repo['id']}")
    if output.exists():
        raise ValueError(f"Refusing to overwrite packet: {output}")
    # Validate everything before creating a packet that might look complete.
    sources = {}
    for repo in repos:
        documents = load_documents(root / ".research/corpora" / repo["id"])
        if not documents:
            raise ValueError(f"Empty source snapshot: {repo['id']}")
        sources[repo["id"]] = documents
    output.mkdir(parents=True)
    manifest = {}
    for repo, documents in sources.items():
        manifest[repo] = {}
        for relative, text in documents.items():
            destination = output / "sources" / repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
            manifest[repo][relative] = sha(destination)
    write_json(output / "source-manifest.json", manifest)
    (output / "AUTHORING.md").write_text(author_instructions(spec))
    # Neither implementation nor comparative/pilot results enter this packet.
    packet = {"schema": 1, "id": spec["id"], "repositories": repos,
              "category_quota": spec["category_quota"],
              "minimum_scored_queries": spec["minimum_scored_queries"],
              "source_manifest_sha256": sha(output / "source-manifest.json"),
              "instructions_sha256": sha(output / "AUTHORING.md"),
              "status": "awaiting_independent_authoring",
              "retrieval_has_run": False, "product_promotion_allowed": False}
    write_json(output / "packet.json", packet)
    return {"packet_sha256": sha(output / "packet.json"), "packet": str(output),
            "source_files": {r: len(d) for r, d in manifest.items()},
            "exposure": exposure_receipt(exposure), "status": packet["status"],
            "product_promotion_allowed": False}


def check_packet(packet_path):
    """Authenticate the packet itself before checking author declarations."""
    packet = json.loads(packet_path.read_text())
    directory = packet_path.parent
    errors = []
    for name, key in (("source-manifest.json", "source_manifest_sha256"),
                      ("AUTHORING.md", "instructions_sha256")):
        if not (directory / name).is_file() or sha(directory / name) != packet[key]:
            errors.append(f"Packet {name} hash mismatch")
    if errors:
        return packet, {}, errors
    manifest = json.loads((directory / "source-manifest.json").read_text())
    for repo, documents in manifest.items():
        for path, digest in documents.items():
            source = directory / "sources" / repo / path
            if (source.is_symlink() or not source.is_file() or
                    not source.resolve().is_relative_to((directory / "sources").resolve()) or
                    sha(source) != digest):
                errors.append(f"Changed packet source: {repo}/{path}")
    return packet, manifest, errors


def validate(packet_path, queries_path, review_path, expected_packet_sha256, historical_queries=()):
    if sha(packet_path) != expected_packet_sha256:
        return ["Packet differs from the frozen preparation receipt"]
    packet, manifest, errors = check_packet(packet_path)
    if errors:
        return errors
    cases = json.loads(queries_path.read_text())
    review = json.loads(review_path.read_text())
    if not isinstance(cases, list):
        return ["Questions must be an array"]
    if not isinstance(review, dict):
        return ["Authoring review must be an object"]
    for field in ("independent_of_tuning", "source_only"):
        if review.get(field) is not True:
            errors.append(f"Missing author declaration: {field}")
    if not isinstance(review.get("author_id"), str) or not review["author_id"].strip():
        errors.append("Missing author_id")
    if review.get("packet_sha256") != sha(packet_path):
        errors.append("Author review references a different packet")
    if review.get("queries_sha256") != sha(queries_path):
        errors.append("Author review references different questions")
    ids, questions = Counter(), Counter()
    counts = {r: Counter() for r in manifest}
    scored = 0
    for index, case in enumerate(cases):
        label = f"case {index}"
        if not isinstance(case, dict) or set(case) != CASE_FIELDS:
            errors.append(f"{label}: invalid case fields")
            continue
        repo, identifier = case["repo"], case["id"]
        if not isinstance(repo, str) or repo not in manifest:
            errors.append(f"{label}: unknown repository")
            continue
        if not isinstance(identifier, str) or not identifier.startswith(f"h-{repo}-"):
            errors.append(f"{label}: invalid id")
            continue
        label = identifier
        ids[identifier] += 1
        if not isinstance(case["category"], str) or case["category"] not in packet["category_quota"]:
            errors.append(f"{label}: invalid category")
            continue
        counts[repo][case["category"]] += 1
        if not isinstance(case["query"], str) or not case["query"].strip():
            errors.append(f"{label}: empty question")
        else:
            question = normalized(case["query"])
            questions[question] += 1
            if question in historical_queries:
                errors.append(f"{label}: historical question reused")
        if case["annotation_status"] != STATUS:
            errors.append(f"{label}: annotation_status")
        negative = case["category"] == "no_evidence"
        expected = "insufficient_repository_evidence" if negative else "retrieve_evidence"
        evidence = case["evidence"]
        if (not isinstance(evidence, list) or case["expected"] != expected or
                negative != (not evidence)):
            errors.append(f"{label}: inconsistent expected/evidence")
            continue
        if negative:
            rationales = review.get("no_evidence_rationales", {})
            rationale = rationales.get(identifier) if isinstance(rationales, dict) else None
            if not isinstance(rationale, str) or len(rationale.strip()) < 40:
                errors.append(f"{label}: negative control needs source-search rationale")
        code_files = set()
        for item in evidence:
            if not isinstance(item, dict):
                errors.append(f"{label}: invalid evidence object")
                continue
            path = item.get("path")
            if not isinstance(path, str) or path not in manifest[repo]:
                errors.append(f"{label}: evidence path outside packet")
                continue
            if item.get("file_sha256") != manifest[repo][path]:
                errors.append(f"{label}: source hash mismatch for {path}")
            lines = (packet_path.parent / "sources" / repo / path).read_text().splitlines()
            start, end = item.get("start"), item.get("end")
            if not (type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)):
                errors.append(f"{label}: invalid source range")
                continue
            anchor = item.get("anchor")
            if not isinstance(anchor, str) or not anchor or anchor not in lines[start - 1]:
                errors.append(f"{label}: anchor not on start line")
            if Path(path).suffix not in DOC_SUFFIXES:
                code_files.add(path)
        if not negative and not code_files:
            errors.append(f"{label}: answerable question needs code evidence")
        if code_files:
            scored += 1
    errors += [f"Duplicate id: {k}" for k, v in ids.items() if v > 1]
    errors += [f"Duplicate question: {k}" for k, v in questions.items() if v > 1]
    for repo, actual in counts.items():
        if dict(actual) != packet["category_quota"]:
            errors.append(f"{repo}: category quota mismatch")
    if scored < packet["minimum_scored_queries"]:
        errors.append(f"Only {scored} scored questions; need {packet['minimum_scored_queries']}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--receipt", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--packet", type=Path, required=True)
    check.add_argument("--packet-sha256", required=True,
                       help="Expected hash from the frozen preparation receipt")
    check.add_argument("--queries", type=Path, required=True)
    check.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.spec, args.output)
        write_json(args.receipt, result)
        print(json.dumps({k: v for k, v in result.items() if k != "exposure"}, indent=2))
        return 0
    errors = validate(args.packet, args.queries, args.review, args.packet_sha256,
                      set(historical_exposure(BASE)["queries"]))
    print(json.dumps({"status": "invalid" if errors else "structurally_valid_pending_source_review",
                      "errors": errors, "product_promotion_allowed": False}, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
