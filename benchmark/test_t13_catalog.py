"""t13's pool excludes every t12 task and any task that shares an issue with one."""
import unittest

from t13_catalog import unused


class T13CatalogTest(unittest.TestCase):
    def test_drops_used_tasks_shared_issues_and_emptied_repositories(self):
        found = {"a": [{"id": "a-1", "issue": "i1"}, {"id": "a-2", "issue": "i2"}, {"id": "a-3", "issue": "i3"}],
                 "b": [{"id": "b-1", "issue": "j1"}]}
        used = [{"id": "a-1", "issue": "i1"}, {"id": "x-9", "issue": "i2"}, {"id": "b-1", "issue": "j1"}]
        self.assertEqual(unused(found, used), {"a": [{"id": "a-3", "issue": "i3"}]})


if __name__ == "__main__":
    unittest.main()
