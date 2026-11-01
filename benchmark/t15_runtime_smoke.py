"""Smoke-test the t15 runtimes on unmodified pinned snapshots, with test files that are no candidate's hidden tests.

    python benchmark/t15_runtime_smoke.py [repo ...]

As benchmark/t14_runtime_smoke.py; each file was chosen as the lower-quartile-sized test file of the repository (under
the named directory) that no t15 candidate changes; go-swagger's first pick, generator/client_harness_test.go, ran the
whole generator package past 300 s (no candidate tests it), so the smoke uses the candidates' own funcmaps package. Writes .research/history/t15-runtime-smoke.json.
"""
import json
import shutil
import sys

from t12_runtime_smoke import BASE, WORK, smoke

SMOKE = {
    "env": ["env_tomap_test.go"], "carbon": ["calendar/gregorian_bench_test.go"], "lancet": ["pointer/pointer_test.go"],
    "gocron": ["logger_test.go"], "revive": ["test/waitgroup_by_value_test.go"], "fq": ["internal/ctxstack/ctxstacl_test.go"],
    "oapi-codegen": ["pkg/codegen/filter_test.go"], "go-git": ["plumbing/transport/version_test.go"],
    "compress": ["zstd/decodeheader_test.go"], "vale": ["internal/system/zip_test.go"],
    "go-swagger": ["generator/internal/funcmaps/golang/comments_test.go"], "dns": ["labels_test.go"],
    "tenacity": ["tests/test_tornado.py"], "sqlglot": ["tests/dialects/test_dax.py"],
    "apscheduler": ["tests/test_serializers.py"], "falcon": ["tests/test_media_urlencoded.py"],
    "django-ninja": ["tests/test_schema_context.py"], "streamlink": ["tests/plugins/test_sportschau.py"],
    "trio": ["src/trio/_tests/test_tracing.py"], "sqlfluff": ["test/rules/std_ST05_LT09_test.py"],
    "node-semver": ["test/functions/patch.js"], "chroma": ["test/delta-e.test.js"],
    "svgo": ["test/svgo/billion-laughs.test.js"], "sinon": ["test/src/util/core/times-in-words-test.js"],
    "formidable": ["test/standalone/issue-46.test.js"], "pdfkit": ["tests/unit/trailer.spec.js"],
    "cytoscape": ["test/modules/edge-control-points.mjs"],
}


def main(repos):
    out = BASE / ".research/history/t15-runtime-smoke.json"
    results = json.loads(out.read_text()) if out.is_file() else {}
    for repo in repos or SMOKE:
        results[repo] = smoke(repo, SMOKE[repo])
        print(repo, results[repo]["returncode"], results[repo]["wall_s"], flush=True)
    out.write_text(json.dumps(results, indent=1) + "\n")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
