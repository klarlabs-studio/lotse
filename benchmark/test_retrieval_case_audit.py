import hashlib
import json
from pathlib import Path

import pytest

import retrieval_case_audit as audit


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path, *, inconsistent=False, bad_metric=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    audit.BASE = tmp_path
    query = [{"id": "q1", "repo": "repo", "evidence": [{"path": "src/a.py"}, {"path": "docs/guide.md"}, {"path": "src/b.py"}]}]
    queries = tmp_path / "queries.json"
    queries.write_text(json.dumps(query))
    root = tmp_path / "scratch" / "repo" / "input"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src/a.py").write_text("a\n")
    (root / "src/b.py").write_text("b\n")
    (root / "docs/guide.md").write_text("doc\n")
    hashes = {p.relative_to(root).as_posix(): _sha(p) for p in root.rglob("*") if p.is_file()}
    graf_first = ('"src/a.py":1-1\na\n\n' * 6) + '"docs/guide.md":1-1\ndoc\n\n"src/b.py":1-1\nb\n\n'
    graf_second = '"src/b.py":1-1\nb\n\n"src/a.py":1-1\na\n\n"docs/guide.md":1-1\ndoc\n\n'
    graphify_text = "[src=src/a.py loc=L1 community=]\n[src=docs/guide.md loc=L1 community=]\n[src=src/b.py loc=L1 community=]"
    def tool(arm, stdout_runs, order, tokens):
        stored_order = order
        recall = len(set(order) & {"src/a.py", "src/b.py"}) / 2
        at5 = recall if recall is not None else None
        return {"runs": [{"stdout": s, "stderr": "", "returncode": 0} for s in stdout_runs],
                "cited_path_order": stored_order,
                "code_file_hint_recall": 0.5 if bad_metric and arm == "graf" else recall,
                "code_file_hint_recall_at_5": at5,
                "code_file_hint_recall_at_10": at5,
                "output_tokens": tokens}
    g_order = ["src/a.py", "docs/guide.md", "src/b.py"]
    graph_order = ["src/a.py", "docs/guide.md", "src/b.py"]
    tools = {"graf": tool("graf", [graf_first, graf_second if inconsistent else graf_first], g_order, 30),
             "graphify": tool("graphify", [graphify_text, graphify_text], graph_order, 20)}
    row = {"id": "q1", "repo": "repo", "budget": 2000, "tools": tools}
    second_row = {**row, "budget": 8000}
    comp = {"scratch": "scratch", "query_sha256": _sha(queries), "repeats": 2,
            "arms": {"graf": "full excerpts", "graphify": "navigation hints"},
            "repositories": {"repo": {"file_hashes": hashes}},
            "rows": [row, second_row]}
    comp_path = tmp_path / "comparison.json"
    comp_path.write_text(json.dumps(comp))
    return comp_path, queries


def test_audit_reports_rank_docs_and_repeated_citations(tmp_path):
    comp, queries = _fixture(tmp_path)
    result = audit.audit(comp, queries)
    graf = result["cases"][0]["arms"]["graf"]
    assert graf["expected_ranks"] == {"src/a.py": 1, "src/b.py": 3}
    assert graf["citation_sections"][:2] == [
        {"path": "src/a.py", "start": 1, "end": 1},
        {"path": "src/a.py", "start": 1, "end": 1},
    ]
    assert graf["repeated_path_section_count"] == 5
    assert graf["path_section_counts"]["src/a.py"] == 6
    assert graf["documentation_or_test_sections"] == [
        {"path": "docs/guide.md", "position": 7, "documentation_heuristic": True, "test_heuristic": False}
    ]
    assert graf["flags"]["docs_in_top5"] is True
    assert result["cases"][0]["arms"]["graphify"]["graphify_raw_distinct_path_count"] == 3


def test_rejects_repetition_order_disagreement_and_bad_stored_metric(tmp_path):
    comp, queries = _fixture(tmp_path / "repetition", inconsistent=True)
    with pytest.raises(ValueError, match="varies across repetitions"):
        audit.audit(comp, queries)
    comp, queries = _fixture(tmp_path / "metric", bad_metric=True)
    with pytest.raises(ValueError, match="stored code_file_hint_recall mismatch"):
        audit.audit(comp, queries)


def test_rejects_missing_row_arm_and_repetition(tmp_path):
    comp, queries = _fixture(tmp_path / "row")
    data = json.loads(comp.read_text())
    data["rows"].pop()
    comp.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="cover every query"):
        audit.audit(comp, queries)

    comp, queries = _fixture(tmp_path / "arm")
    data = json.loads(comp.read_text())
    del data["rows"][0]["tools"]["graphify"]
    comp.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="arm set mismatch"):
        audit.audit(comp, queries)

    comp, queries = _fixture(tmp_path / "repeat")
    data = json.loads(comp.read_text())
    data["rows"][0]["tools"]["graf"]["runs"].pop()
    comp.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="repetition count mismatch"):
        audit.audit(comp, queries)

