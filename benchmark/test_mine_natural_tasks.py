"""Reverting a real fix must be exact: every hunk maps back to the pre-fix text once."""
import unittest

from mine_natural_tasks import classify, clean_issue, revert_edits

PRE = "def value():\n    return 2\n\n\nx\n" + "y\n" * 4 + "    a = 1\n    b = 2\n    return a + b\n"
POST = PRE.replace("return 2", "return fixed()").replace("    b = 2\n", "    b = 2\n    c = 3\n")


def apply(text, edits):
    for edit in edits:
        assert text.count(edit["before"]) == 1
        text = text.replace(edit["before"], edit["after"], 1)
    return text


class MinerTests(unittest.TestCase):
    def test_edits_turn_the_pinned_file_back_into_the_pre_fix_file(self):
        edits = revert_edits("app.py", PRE, POST, POST)
        self.assertEqual(len(edits), 2)
        self.assertEqual(apply(POST, edits), PRE)

    def test_each_region_shrinks_its_context_when_the_code_drifted(self):
        drifted = POST.replace("    a = 1\n", "    a = 11\n").replace("    return a + b\n", "    return a * b\n")
        edits = revert_edits("app.py", PRE, POST, drifted)
        self.assertEqual(apply(drifted, edits), PRE.replace("    a = 1\n", "    a = 11\n").replace("+ b", "* b"))
        self.assertEqual(edits[1], {"path": "app.py", "before": "    c = 3\n", "after": ""})

    def test_ambiguous_or_missing_regions_reject(self):
        self.assertIsNone(revert_edits("app.py", PRE, POST, "unrelated\n"))
        self.assertIsNone(revert_edits("app.py", PRE, POST, POST + POST))

    def test_classify_paths_per_repository(self):
        self.assertEqual(classify("cobra", "command_test.go"), "test")
        self.assertEqual(classify("cobra", "command.go"), "source")
        self.assertEqual(classify("flask", "tests/test_basic.py"), "test")
        self.assertEqual(classify("flask", "src/flask/app.py"), "source")
        self.assertEqual(classify("flask", "CHANGES.rst"), "other")
        self.assertEqual(classify("fastify", "test/hooks.test.js"), "test")
        self.assertEqual(classify("fastify", "lib/reply.js"), "source")
        self.assertEqual(classify("fastify", "test/types/reply.test-d.ts"), "test")
        self.assertEqual(classify("gin", "context_test.go"), "test")
        self.assertEqual(classify("urfave-cli", "command.go"), "source")
        self.assertEqual(classify("click", "src/click/core.py"), "source")
        self.assertEqual(classify("jinja", "tests/test_api.py"), "test")
        self.assertEqual(classify("express", "lib/response.js"), "source")
        self.assertEqual(classify("undici", "test/fetch/client.js"), "test")

    def test_typescript_classification(self):
        self.assertEqual(classify("zod", "packages/zod/src/v4/core/schemas.ts"), "source")
        self.assertEqual(classify("tanstack-query", "packages/react-query/src/useQuery.tsx"), "source")
        self.assertEqual(classify("zod", "packages/zod/src/v4/classic/tests/string.test.ts"), "test")
        self.assertEqual(classify("hono", "src/hono.spec.ts"), "test")
        self.assertEqual(classify("trpc", "packages/server/src/__tests__/x.ts"), "test")
        self.assertEqual(classify("typeorm", "test/functional/x.ts"), "test")
        self.assertEqual(classify("vitest", "docs/guide/index.md"), "other")

    def test_verify_commands_per_runner(self):
        from mine_natural_tasks import verify_command
        self.assertEqual(verify_command("gin", ["render/render_test.go"]), ["go", "test", "./render", "-count=1"])
        self.assertEqual(verify_command("click", ["tests/test_basic.py"])[-1], "tests/test_basic.py")
        self.assertEqual(verify_command("express", ["test/res.send.js", "test/support/utils.js"]),
                         ["node_modules/.bin/mocha", "--require", "test/support/env", "--check-leaks", "test/res.send.js"])
        self.assertEqual(verify_command("undici", ["test/fetch/a.js"]), ["node", "--test", "test/fetch/a.js"])

    def test_t4_repositories_resolve_to_their_pins(self):
        from mine_natural_tasks import pinned
        self.assertEqual(pinned("undici")["repository"], "nodejs/undici")
        self.assertEqual(pinned("cobra")["repository"], "spf13/cobra")
        self.assertEqual(pinned("axios")["repository"], "axios/axios")
        self.assertEqual(classify("httpx", "tests/test_api.py"), "test")
        self.assertEqual(classify("chi", "mux.go"), "source")

    def test_issue_text_is_verbatim_without_comments_and_bounded(self):
        text = clean_issue("Title", "Body <!-- template -->line\r\n" + "x" * 5000)
        self.assertTrue(text.startswith("Title\n\nBody line\n"))
        self.assertNotIn("template", text)
        self.assertLessEqual(len(text), 4000 + len("\n[truncated]"))
        self.assertTrue(text.endswith("[truncated]"))


if __name__ == "__main__":
    unittest.main()
