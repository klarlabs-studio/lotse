"""Materialize manually selected evidence anchors before running retrievers.

These are exploratory relevance judgments, not an independently reviewed test set.
Retrieval implementations must never read the evidence fields while ranking.
"""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

# Each evidence unit is a source line containing a manually selected anchor.
# Units are deliberately minimal; matching one does not establish answer sufficiency.
CASES = {
    "cobra": [
        ("exact", "Locate the ExactArgs implementation and its invalid-count test.", [("args.go", "func ExactArgs("), ("args_test.go", "func TestExactArgs_WithInvalidCount(")]),
        ("exact", "Locate ExecuteContext and the test for executing with a context.", [("command.go", "func (c *Command) ExecuteContext("), ("command_test.go", "func TestExecuteContext(")]),
        ("conceptual", "How can a command require username and password to be supplied together?", [("site/content/user_guide.md", 'rootCmd.MarkFlagsRequiredTogether("username", "password")'), ("flag_groups.go", "func (c *Command) MarkFlagsRequiredTogether(")]),
        ("conceptual", "How can a child command parse flags belonging to its parent?", [("site/content/user_guide.md", "parent commands are ignored."), ("command.go", "if c.TraverseChildren {")]),
        ("architecture", "Explain the order and inheritance of persistent pre-run hooks during command execution.", [("site/content/user_guide.md", "Set `EnableTraverseRunHooks`"), ("command.go", "if p.PersistentPreRunE != nil {")]),
        ("impact", "If positional argument count validation changes, which MinimumNArgs implementation and boundary test need inspection?", [("args.go", "func MinimumNArgs("), ("args_test.go", "func TestMinimumNArgs_WithLessArgs(")]),
        ("trace", "Trace the requirement that mutually exclusive flags cannot be supplied together to its enforcement and tests.", [("site/content/user_guide.md", "rootCmd.MarkFlagsMutuallyExclusive("), ("flag_groups.go", "func validateExclusiveFlagGroups("), ("flag_groups_test.go", "func TestValidateFlagGroups(")]),
        ("trace", "Find the Markdown documentation generation guide, implementation, and tests.", [("site/content/docgen/md.md", "GenMarkdownTree"), ("doc/md_docs.go", "func GenMarkdownTree("), ("doc/md_docs_test.go", "func TestGenMdTree(")]),
        ("discrepancy", "Check the claim that ExecuteContext preserves an earlier SetContext value against the source contract and implementation.", [("command.go", "// Command.ExecuteContext or Command.ExecuteContextC."), ("command.go", "func (c *Command) ExecuteContext(" )]),
        ("no_evidence", "What approved customer pricing tiers and enterprise subscription prices will Cobra launch next quarter?", []),
    ],
    "flask": [
        ("exact", "Locate ensure_sync and the async route test.", [("src/flask/app.py", "    def ensure_sync("), ("tests/test_async.py", "def test_async_route(")]),
        ("exact", "Locate SecureCookieSessionInterface.get_signing_serializer and the secret key fallback test.", [("src/flask/sessions.py", "    def get_signing_serializer("), ("tests/test_basic.py", "def test_session_secret_key_fallbacks(")]),
        ("conceptual", "Why do background asyncio tasks spawned by an async view get cancelled?", [("docs/async-await.rst", "will be cancelled."), ("src/flask/app.py", "        return asgiref_async_to_sync(func)")]),
        ("conceptual", "How can session signing keys be rotated without invalidating existing cookies?", [("docs/config.rst", ".. py:data:: SECRET_KEY_FALLBACKS"), ("src/flask/sessions.py", '        if fallbacks := app.config["SECRET_KEY_FALLBACKS"]:')]),
        ("architecture", "Explain where response processing saves sessions in the request lifecycle.", [("docs/lifecycle.rst", "#.  The session is saved"), ("src/flask/app.py", "            self.session_interface.save_session(")]),
        ("impact", "If after_request callback execution order changes, which response processor and ordering test should be inspected?", [("src/flask/app.py", "                for func in reversed(self.after_request_funcs[name]):"), ("tests/test_basic.py", "def test_before_after_request_order(")]),
        ("trace", "Trace the request body size limit requirement to the request implementation and tests.", [("docs/config.rst", ".. py:data:: MAX_CONTENT_LENGTH"), ("src/flask/wrappers.py", "    def max_content_length("), ("tests/test_request.py", "def test_max_content_length(")]),
        ("trace", "Trace the documented session key fallback support to implementation and regression tests.", [("docs/config.rst", ".. py:data:: SECRET_KEY_FALLBACKS"), ("src/flask/sessions.py", "            keys.extend(fallbacks)"), ("tests/test_basic.py", "def test_session_secret_key_fallbacks(")]),
        ("discrepancy", "Check the claim that after_request callbacks run in registration order against the source contract and implementation.", [("src/flask/app.py", "           execution are called in reverse order of registration."), ("src/flask/app.py", "                for func in reversed(self.after_request_funcs[name]):")]),
        ("no_evidence", "What customer research approved a paid Flask enterprise plan for next quarter, and what is its subscription price?", []),
    ],
    "fastify": [
        ("exact", "Locate decorate and the duplicate decorator error it raises.", [("lib/decorate.js", "function decorate ("), ("lib/decorate.js", "    throw new FST_ERR_DEC_ALREADY_PRESENT(name)")]),
        ("exact", "Locate validateBodyLimitOption and its invalid bodyLimit error.", [("lib/route.js", "function validateBodyLimitOption ("), ("lib/route.js", "    throw new FST_ERR_ROUTE_BODY_LIMIT_OPTION_NOT_INT(bodyLimit)")]),
        ("conceptual", "Why is a shared object disallowed as a request decorator, and how should per-request values be created?", [("docs/Reference/Decorators.md", "To achieve proper encapsulation across requests"), ("lib/decorate.js", "function checkReferenceType (")]),
        ("conceptual", "How can a route handle schema validation errors itself instead of using the default error handler?", [("docs/Reference/Validation-and-Serialization.md", "To handle errors inside the route"), ("lib/handle-request.js", "attachValidation")]),
        ("architecture", "Explain plugin encapsulation and where a child instance is created.", [("docs/Reference/Encapsulation.md", 'A fundamental feature of Fastify'), ("lib/plugin-override.js", "Object.create(old)")]),
        ("impact", "If bodyLimit validation changes, which route validator and request body limit tests should be inspected?", [("lib/route.js", "function validateBodyLimitOption ("), ("test/body-limit.test.js", "bodyLimit")]),
        ("trace", "Trace trustProxy configuration from the server documentation to request implementation and tests.", [("docs/Reference/Server.md", "### `trustProxy`"), ("lib/request.js", "trustProxy"), ("test/trust-proxy.test.js", "trustProxy")]),
        ("trace", "Trace the requirement to reject duplicate decorators to implementation and tests.", [("docs/Reference/Decorators.md", "with the same name more than once"), ("lib/decorate.js", "    throw new FST_ERR_DEC_ALREADY_PRESENT(name)"), ("test/throw.test.js", "test('Should throw on duplicate decorator',")]),
        ("discrepancy", "Check the claim that bodyLimit can be zero against the documented contract and route validation.", [("docs/Reference/Server.md", "### `bodyLimit`"), ("lib/route.js", "  if (!Number.isInteger(bodyLimit) || bodyLimit <= 0) {")]),
        ("no_evidence", "Which approved customer interviews set Fastify enterprise pricing and next quarter's paid subscription tiers?", []),
    ],
}


def main():
    output = []
    for repo, cases in CASES.items():
        for i, (category, query, refs) in enumerate(cases, 1):
            evidence = []
            for path, anchor in refs:
                content = (BASE / ".research/corpora" / repo / path).read_text()
                lines = content.splitlines()
                matches = [j + 1 for j, line in enumerate(lines) if anchor in line]
                if not matches:
                    raise ValueError(f"Missing anchor {repo}/{path}: {anchor}")
                line = matches[0]
                evidence.append(dict(path=path, start=line, end=line, anchor=anchor,
                                     file_sha256=hashlib.sha256(content.encode()).hexdigest()))
            output.append(dict(id=f"{repo}-{i:02d}", repo=repo, category=category,
                               query=query, evidence=evidence,
                               expected="insufficient_repository_evidence" if not refs else "retrieve_evidence",
                               annotation_status="author_selected_exploratory"))
    (BASE / "benchmark/queries.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
