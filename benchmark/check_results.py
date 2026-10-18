"""Audit saved measurements against current source snapshots and rendered contexts."""
import hashlib
import json
from pathlib import Path

from retrieval import BASE, chunks_from, load_documents, score_evidence, tokens


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    summary = json.loads((BASE / "results/summary.json").read_text())
    env = summary["environment"]
    assert env["query_sha256"] == sha(BASE / "benchmark/queries.json")
    assert env["retriever_sha256"] == sha(BASE / "benchmark/retrieval.py")
    assert env["model_artifacts_sha256"] == sha(BASE / "results/model-artifacts.json")
    cases = {q["id"]: q for q in json.loads((BASE / "benchmark/queries.json").read_text())}
    count = 0
    for repo in ["cobra", "flask", "fastify"]:
        files = load_documents(BASE / ".research/corpora" / repo)
        chunks = [c for p, text in files.items() for c in chunks_from(p, text)]
        lookup = {(c["path"], c["start"], c["end"]): i for i, c in enumerate(chunks)}
        rows = json.loads((BASE / f"results/{repo}.json").read_text())["queries"]
        assert len(rows) == 120
        assert len({(r["query_id"], r["method"], r["budget"]) for r in rows}) == 120
        for row in rows:
            selected = [lookup[(c["path"], c["start"], c["end"])] for c in row["selected"]]
            expected = score_evidence(chunks, selected, cases[row["query_id"]]["evidence"])
            for key, value in expected.items():
                assert row[key] == value
            context = (BASE / ".research/contexts" / repo / f"{row['query_id']}-{row['method']}-{row['budget']}.txt").read_text()
            assert len(tokens(context)) == row["output_tokens"] <= row["budget"]
            count += 1
    graphify = json.loads((BASE / "results/graphify-provenance.json").read_text())
    assert graphify["query_sha256"] == sha(BASE / "benchmark/queries.json")
    assert graphify["driver_sha256"] == sha(BASE / "benchmark/graphify_baseline.py")
    assert len(json.loads((BASE / "results/ripgrep.json").read_text())["queries"]) == 60
    assert all(r["sanity_passed"] for r in json.loads((BASE / "results/task-sanity.json").read_text()))
    print(f"Audited {count} source-context results, 60 native search results, Graphify provenance, and six fixture checks")


if __name__ == "__main__":
    main()
