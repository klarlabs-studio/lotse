import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_readiness import evaluate


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "benchmark").mkdir()
        (self.base / "benchmark/corpora.json").write_text(json.dumps({"repositories": [{"id": "graphify", "repository": "Graphify-Labs/graphify", "commit": "a" * 40}]}))
        (self.base / "tasks.json").write_text("[]")
        (self.base / "sources.txt").write_text("frozen")
        arms = []
        for ident, cmd in (("graf_v59", "evidence-rescue"), ("graf_v61", "evidence-selective"), ("graphify", None)):
            path = self.base / ident
            path.write_text(ident)
            arm = {"id": ident, "binary": ident, "binary_sha256": self.sha(path), "source_reads": True}
            if cmd: arm["query_args"] = [cmd, "{query}", "--budget", "{budget}", "--limit", "20", "--text"]
            else: arm["query_args"] = ["query", "{query}", "--graph", "{graph}", "--budget", "{budget}"]
            arms.append(arm)
        self.plan = {"schema_version": 1, "id": "fixture", "scope": "preparation_only",
            "solver": {"model": "test-model", "reasoning_effort": "medium", "accounting": {k: True for k in ("provider_token_totals", "internal_model_request_count", "all_tool_payloads", "failed_attempts")}, "limits": {"max_actions": 5, "timeout_seconds": 60, "token_limit_kind": "hard", "token_limit": 1000}},
            "tasks": {"catalog": "tasks.json", "sha256": self.sha(self.base / "tasks.json"), "fresh": True, "outcome_checks": True, "independent_review": True}, "arms": arms,
            "graphify_pin": {"repository": "Graphify-Labs/graphify", "commit": "a" * 40}, "graphify_adapter": "native-cli-v2",
            "comparison": {"paired_repetitions": 2, "seed": 7, "sequential": True, "shared_source_manifest": "sources.txt", "shared_source_sha256": self.sha(self.base / "sources.txt"), "shared_tokenizer": "cl100k_base", "shared_output_budget": 4000, "cache_conditions": "cold-cache", "quality_criteria": {"source_anchor_recall": {"margin": 0.1}}, "primary_metric": "total_provider_tokens", "graphify_scope": "code_only_no_clustering"}}

    def tearDown(self): self.tmp.cleanup()

    @staticmethod
    def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_valid_plan_is_readiness_only(self):
        result = evaluate(self.plan, self.base)
        self.assertTrue(result["readiness_pass"], result["blockers"])
        self.assertFalse(result["product_promotion_pass"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["readiness_kind"], "declared_prerequisites_only")
        self.assertEqual(result["agent_savings"], "unmeasured")

    def test_missing_accounting_and_task_outcomes_fail_closed(self):
        self.plan["solver"]["accounting"].pop("failed_attempts")
        self.plan["tasks"]["outcome_checks"] = False
        result = evaluate(self.plan, self.base)
        self.assertFalse(result["readiness_pass"])
        self.assertFalse(result["checks"]["accounting"])
        self.assertFalse(result["checks"]["tasks"])

    def test_pin_hash_and_actual_graf_cli_flags_are_checked(self):
        self.plan["graphify_pin"]["commit"] = "b" * 40
        self.plan["arms"][0]["binary_sha256"] = "0" * 64
        self.plan["arms"][1]["query_args"] = ["units", "--text", "--evidence", "selective"]
        result = evaluate(self.plan, self.base)
        self.assertFalse(result["checks"]["graphify_pin"])
        self.assertFalse(result["checks"]["arms"])

    def test_malformed_plan_does_not_raise(self):
        result = evaluate({"arms": [None]}, self.base)
        self.assertFalse(result["readiness_pass"])
        self.assertFalse(result["product_promotion_pass"])

    def test_unhashable_arm_ids_and_nul_paths_fail_closed(self):
        self.plan["arms"][0]["id"] = []
        self.plan["tasks"]["catalog"] = "bad\x00path"
        result = evaluate(self.plan, self.base)
        self.assertFalse(result["checks"]["arms"])
        self.assertFalse(result["checks"]["tasks"])

    def test_traversal_bad_pin_and_malformed_corpora_fail_closed(self):
        self.plan["tasks"]["catalog"] = "../outside.json"
        self.plan["graphify_pin"] = {}
        (self.base / "benchmark/corpora.json").write_text("{}")
        result = evaluate(self.plan, self.base)
        self.assertFalse(result["checks"]["tasks"])
        self.assertFalse(result["checks"]["graphify_pin"])

    def test_boolean_schema_version_nonfinite_and_nonstring_solver_fail(self):
        self.plan["schema_version"] = True
        self.plan["solver"]["model"] = {"name": "model"}
        self.plan["comparison"]["quality_criteria"] = {"margin": float("nan")}
        result = evaluate(self.plan, self.base)
        self.assertFalse(result["checks"]["schema"])
        self.assertFalse(result["checks"]["solver"])

    def test_observed_token_limit_is_declared_but_not_enforceable(self):
        self.plan["solver"]["limits"]["token_limit_kind"] = "observed_after_turn"
        result = evaluate(self.plan, self.base)
        self.assertFalse(result["checks"]["enforceable_limits"])
        self.assertIn("not enforceable", " ".join(result["blockers"]))

    def test_shared_budget_above_supported_max_fails(self):
        self.plan["comparison"]["shared_output_budget"] = 8001
        result = evaluate(self.plan, self.base)
        self.assertFalse(result["checks"]["comparison"])


if __name__ == "__main__": unittest.main()
