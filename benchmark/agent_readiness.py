"""Fail-closed readiness check for a future matched agent trial; never runs a solver."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from agent_backend import QUERY_ARGS

BASE = Path(__file__).resolve().parent.parent
SHA = re.compile(r"^[0-9a-f]{64}$")


def _safe_file(base, value, allow_absolute=False):
    if not isinstance(value, str) or not value:
        return None
    p = Path(value)
    if p.is_absolute() and not allow_absolute:
        return None
    p = p if p.is_absolute() else base / p
    try:
        p = p.resolve(strict=True)
        if not allow_absolute and not p.is_relative_to(base.resolve()):
            return None
        return p if p.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _hash(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _hash_matches(path, digest):
    try:
        return path is not None and isinstance(digest, str) and SHA.fullmatch(digest) is not None and _hash(path) == digest
    except (OSError, ValueError):
        return False


def _finite_tree(value):
    if isinstance(value, float): return math.isfinite(value)
    if isinstance(value, dict): return all(_finite_tree(k) and _finite_tree(v) for k, v in value.items())
    if isinstance(value, (list, tuple)): return all(_finite_tree(x) for x in value)
    return True


def evaluate(plan, base=BASE):
    """Return readiness diagnostics. Malformed input yields blockers, never exceptions."""
    base = Path(base).resolve()
    checks, blockers = {}, []
    def check(name, ok, why):
        checks[name] = bool(ok)
        if not ok: blockers.append(why)
    if not isinstance(plan, dict): plan = {}
    check("schema", type(plan.get("schema_version")) is int and plan.get("schema_version") == 1 and isinstance(plan.get("id"), str) and bool(plan.get("id")) and plan.get("scope") == "preparation_only" and _finite_tree(plan), "plan schema/id/scope must identify schema_version 1 preparation_only and contain only finite numbers")
    solver = plan.get("solver") if isinstance(plan.get("solver"), dict) else {}
    acct = solver.get("accounting") if isinstance(solver.get("accounting"), dict) else {}
    check("solver", isinstance(solver.get("model"), str) and bool(solver.get("model").strip()) and isinstance(solver.get("reasoning_effort"), str) and bool(solver.get("reasoning_effort").strip()), "solver model and reasoning_effort must be nonempty strings")
    needed = ("provider_token_totals", "internal_model_request_count", "all_tool_payloads", "failed_attempts")
    check("accounting", all(acct.get(k) is True for k in needed), "complete accounting for provider totals, model requests, tool payloads, and failed attempts is required")
    limits = solver.get("limits") if isinstance(solver.get("limits"), dict) else {}
    positive = lambda x: isinstance(x, int) and not isinstance(x, bool) and x > 0
    check("limits", positive(limits.get("max_actions")) and positive(limits.get("timeout_seconds")) and positive(limits.get("token_limit")) and limits.get("token_limit_kind") in ("hard", "observed_after_turn"), "positive action/time/token limits and recognized token_limit_kind are required")
    check("enforceable_limits", limits.get("token_limit_kind") == "hard", "token limit must be a hard live cap; observed_after_turn is not enforceable")
    tasks = plan.get("tasks") if isinstance(plan.get("tasks"), dict) else {}
    taskfile = _safe_file(base, tasks.get("catalog"))
    taskhash = tasks.get("sha256")
    check("tasks", _hash_matches(taskfile, taskhash) and tasks.get("fresh") is True and tasks.get("outcome_checks") is True and tasks.get("independent_review") is True, "task catalog must be safely located, hash-pinned, fresh, outcome-checked, and independently reviewed")
    arms = plan.get("arms")
    expected = {"graf_v59", "graf_v61", "graphify"}
    arms_ok = isinstance(arms, list) and len(arms) == 3 and all(isinstance(a, dict) and isinstance(a.get("id"), str) for a in arms) and {a.get("id") for a in arms if isinstance(a, dict) and isinstance(a.get("id"), str)} == expected
    arm_map = {a.get("id"): a for a in arms if isinstance(a, dict) and isinstance(a.get("id"), str)} if isinstance(arms, list) else {}
    per_arm = True
    for ident in expected:
        a = arm_map.get(ident, {})
        binary = _safe_file(base, a.get("binary"), allow_absolute=True)
        digest = a.get("binary_sha256")
        per_arm &= _hash_matches(binary, digest)
        if ident != "graphify":
            args = a.get("query_args")
            per_arm &= args == QUERY_ARGS[ident]
        else:
            per_arm &= a.get("source_reads") is True and a.get("query_args") == QUERY_ARGS[ident]
        if ident.startswith("graf_"):
            per_arm &= a.get("source_reads") is True
    check("arms", arms_ok and per_arm, "exact graf_v59/graf_v61/graphify arms with existing hash-pinned binaries, real CLI arguments and source reads are required")
    pin = plan.get("graphify_pin") if isinstance(plan.get("graphify_pin"), dict) else {}
    try:
        corpora = json.loads((base / "benchmark/corpora.json").read_text())
        pinned = next(x for x in corpora["repositories"] if x["id"] == "graphify")
    except (OSError, ValueError, KeyError, StopIteration, TypeError): pinned = {}
    check("graphify_pin", isinstance(pinned.get("repository"), str) and bool(pinned.get("repository")) and isinstance(pinned.get("commit"), str) and re.fullmatch(r"[0-9a-f]{40}", pinned.get("commit", "")) is not None and pin.get("repository") == pinned.get("repository") and pin.get("commit") == pinned.get("commit"), "Graphify repository and commit must match benchmark/corpora.json pinned baseline")
    cmp = plan.get("comparison") if isinstance(plan.get("comparison"), dict) else {}
    manifest = _safe_file(base, cmp.get("shared_source_manifest")); mh = cmp.get("shared_source_sha256")
    nums = (cmp.get("paired_repetitions"), cmp.get("shared_output_budget"))
    good_criteria = isinstance(cmp.get("quality_criteria"), dict) and bool(cmp.get("quality_criteria"))
    check("comparison", positive(nums[0]) and positive(nums[1]) and nums[1] <= 8000 and isinstance(cmp.get("seed"), int) and not isinstance(cmp.get("seed"), bool) and cmp.get("sequential") is True and _hash_matches(manifest, mh) and cmp.get("shared_tokenizer") == "cl100k_base" and isinstance(cmp.get("cache_conditions"), str) and bool(cmp.get("cache_conditions").strip()) and good_criteria and cmp.get("primary_metric") == "total_provider_tokens", "paired sequential comparison, hashed shared source manifest, tokenizer, budgets <=8000, cache conditions, quality criteria, and primary metric are required")
    check("graphify_scope", plan.get("graphify_adapter") == "native-cli-v2" and cmp.get("graphify_scope") == "code_only_no_clustering", "Graphify must use native-cli-v2 and be labeled code_only_no_clustering")
    return {"readiness_pass": bool(checks) and all(checks.values()), "readiness_kind": "declared_prerequisites_only", "execution_authorized": False, "product_promotion_pass": False, "agent_savings": "unmeasured", "checks": checks, "blockers": blockers, "scope": "preparation_only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text())
    except (OSError, ValueError) as e:
        result = {"readiness_pass": False, "product_promotion_pass": False, "agent_savings": "unmeasured", "checks": {}, "blockers": [f"cannot read plan: {e}"], "scope": "preparation_only"}
    else: result = evaluate(plan)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output: args.output.write_text(rendered)
    else: print(rendered, end="")
    return 0 if result["readiness_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
