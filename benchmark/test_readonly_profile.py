import sys
import time
import types
import unittest

try:
    from benchmark.readonly_profile import ReadonlyProfile
except ImportError:
    from readonly_profile import ReadonlyProfile


class ReadonlyProfileTests(unittest.TestCase):
    def setUp(self):
        self.base = types.ModuleType("profile_test_base")

        def tokens(value):
            time.sleep(0.001)
            return value

        def json_encode(value):
            return value

        base_module = self.base

        def wire(value):
            base_module._json(value)
            return base_module.tokens(value)

        class Broker:
            def _append(self, value):
                return wire(value)

            def _path(self, value):
                if value == "bad":
                    raise LookupError("source validation failed")
                return value

            def _verify_inventory(self):
                return self._path("file")

        self.base.tokens = tokens
        self.base._json = json_encode
        self.base.Broker = Broker
        self.base.Broker._json = json_encode
        self.v2 = types.ModuleType("profile_test_v2")
        self.v2._sha = lambda path: str(path)
        v2_module = self.v2

        class V2Broker(Broker):
            def _check_pins(self):
                self._verify_inventory()
                return v2_module._sha("graph.json")

        self.v2.Broker = V2Broker
        self.v2._sha = lambda path: str(path)
        self.base_name, self.v2_name = "profile_test_base", "profile_test_v2"
        sys.modules[self.base_name] = self.base
        sys.modules[self.v2_name] = self.v2
        self.originals = (self.base.tokens, self.base._json, self.base.Broker._append,
                          self.base.Broker._path, self.v2.Broker._check_pins, self.v2._sha)

    def tearDown(self):
        sys.modules.pop(self.base_name, None)
        sys.modules.pop(self.v2_name, None)

    def profiler(self):
        return ReadonlyProfile(broker_module=self.base_name, v2_module=self.v2_name)

    def test_counts_nested_calls_and_restores_methods(self):
        with self.profiler() as profile:
            self.base._json("wire")
            self.base.tokens("payload")
            self.base.Broker()._append("payload")
            self.v2.Broker()._check_pins()
        report = profile.snapshot()
        categories = report["categories"]
        self.assertEqual(categories["journal_persistence"]["count"], 1)
        self.assertEqual(categories["serialization"]["count"], 2)
        self.assertEqual(categories["tokenization"]["count"], 2)
        self.assertEqual(categories["_check_pins"]["count"], 1)
        self.assertEqual(categories["_verify_inventory"]["count"], 1)
        self.assertEqual(categories["source_validation"]["count"], 1)
        self.assertEqual(categories["sha_artifact"]["count"], 1)
        self.assertLessEqual(sum(v["exclusive_ms"] for v in categories.values()),
                             report["instrumented_root_wall_ms"])
        self.assertEqual((self.base.tokens, self.base._json, self.base.Broker._append,
                          self.base.Broker._path, self.v2.Broker._check_pins, self.v2._sha),
                         self.originals)

    def test_exception_propagates_and_wrappers_restore(self):
        with self.assertRaisesRegex(LookupError, "source validation failed"):
            with self.profiler() as profile:
                self.base.Broker()._path("bad")
        self.assertEqual(profile.snapshot()["categories"]["source_validation"]["count"], 1)
        self.assertEqual(self.base.Broker._path, self.originals[3])

    def test_explicit_nested_measurement_is_disjoint(self):
        with self.profiler() as profile:
            with profile.measure("outer"):
                with profile.measure("child"):
                    time.sleep(0.002)
        categories = profile.snapshot()["categories"]
        self.assertEqual(categories["outer"]["count"], 1)
        self.assertEqual(categories["child"]["count"], 1)
        self.assertLess(categories["outer"]["exclusive_ms"], categories["outer"]["inclusive_ms"])

    def test_nested_profiler_context_is_refused_without_removing_outer_patch(self):
        outer = self.profiler()
        with outer:
            with self.assertRaisesRegex(RuntimeError, "another read-only profile"):
                with self.profiler():
                    pass
            self.base.Broker()._append("outer")
        self.assertEqual(outer.snapshot()["categories"]["journal_persistence"]["count"], 1)
        self.assertEqual(self.base.Broker._append, self.originals[2])


if __name__ == "__main__":
    unittest.main()
