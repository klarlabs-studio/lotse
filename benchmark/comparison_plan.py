"""Authenticated, explicit workload/arm selection for paired comparisons."""
import hashlib
import json
from pathlib import Path
import re

from retrieval import graphify_baselines


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(base, value):
    path = base / value
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("Run-plan path escapes workspace")
    return path


def load_plan(path, prereg, base, known_arms):
    """Fail before builds/queries if any frozen input or configuration changed."""
    spec = json.loads(path.read_text())
    frozen = prereg.get("comparison_plans", {}).get(spec["id"], {})
    if frozen.get("sha256") != sha(path) or frozen.get("path") != path.relative_to(base).as_posix():
        raise ValueError("Comparison plan must be frozen in the active preregistration")
    required_tools = {"benchmark/compare_graphify.py", "benchmark/check_comparison.py",
                      "benchmark/comparison_plan.py"}
    if not required_tools <= set(frozen.get("tools", {})):
        raise ValueError("Comparison plan must authenticate driver, auditor and plan loader")
    for name, digest in frozen.get("tools", {}).items():
        if sha(local(base, name)) != digest:
            raise ValueError(f"Changed comparison tool: {name}")
    arms = spec["arms"]
    if (not isinstance(arms, list) or len(arms) != len(set(arms)) or
            "graphify" not in arms or len(arms) < 2 or
            any(a not in {*known_arms, "graphify"} for a in arms)):
        raise ValueError("Plan needs unique known graf arms and pinned Graphify")
    if type(spec["repeats"]) is not int or spec["repeats"] < 1:
        raise ValueError("Plan repetitions must be a positive integer")
    if spec["budgets"] != [2000, 8000] or spec["tokenizer"] != "cl100k_base":
        raise ValueError("Only shared 2K/8K cl100k budgets are supported")
    local(base, spec["output"])
    inputs = {}
    for key in ("queries", "corpora", "snapshots", *(["source_manifest"] if "source_manifest" in spec else [])):
        value = spec[key]
        source = local(base, value["path"])
        if sha(source) != value["sha256"]:
            raise ValueError(f"Changed frozen {key}")
        inputs[key] = json.loads(source.read_text())
    baseline = json.loads((base / "benchmark/corpora.json").read_text())
    manifests = inputs["corpora"]["repositories"]
    planned = graphify_baselines(inputs["corpora"])
    if not planned or any(r not in graphify_baselines(baseline) for r in planned):
        raise ValueError("Plan cannot replace pinned Graphify")
    repos = [r["id"] for r in manifests if r.get("role") != "baseline"]
    if not repos or len(repos) != len(set(repos)) or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9-]*", r) for r in repos):
        raise ValueError("Invalid or duplicate corpus identities")
    receipts = {r["id"]: r for r in inputs["snapshots"]}
    for item in manifests:
        receipt = receipts.get(item["id"], {})
        if any(receipt.get(k) != item.get(k) for k in ("repository", "commit")):
            raise ValueError(f"Snapshot receipt differs for {item['id']}")
    cases = inputs["queries"]
    if not cases or {c["repo"] for c in cases} != set(repos):
        raise ValueError("Questions must cover exactly the plan's repositories")
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate query IDs")
    if "source_manifest" in inputs and set(inputs["source_manifest"]) != set(repos):
        raise ValueError("Source manifest must cover exactly the plan's repositories")
    binaries = spec["binaries"]
    if set(binaries) != set(arms) - {"graphify"}:
        raise ValueError("Every graf arm needs an authenticated binary")
    for value in [spec["index_binary"], *binaries.values()]:
        if sha(local(base, value["path"])) != value["sha256"]:
            raise ValueError("Changed frozen graf binary")
    return spec, inputs


def verify_sources(inputs, repo, files):
    if "source_manifest" in inputs:
        actual = {p: hashlib.sha256(text.encode()).hexdigest() for p, text in files.items()}
        if actual != inputs["source_manifest"][repo]:
            raise ValueError(f"Sources differ from frozen authoring packet: {repo}")


def selected_layout(arms, known_arms):
    selected = {a: known_arms[a] for a in arms if a != "graphify"}
    # Keep the base index for shared source-set authentication, even when all
    # selected queries use alternate analyzer indexes.
    variants = tuple(dict.fromkeys([(), *(flags for flags, _ in selected.values())]))
    return selected, variants
