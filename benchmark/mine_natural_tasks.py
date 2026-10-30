"""Mine natural repair-task candidates from real, issue-linked upstream fixes.

    python benchmark/mine_natural_tasks.py REPO     # cobra | flask | fastify

Follows agent_trials.natural_tasks in benchmark/preregistration.json. Lists the
repository's merged pull requests that close an issue, newest merge first from
the pin, and applies the automatic criteria: ancestor of the pin, source and
test changes, bounded size, and an exact unique revert on the pinned snapshot.
Every pull request considered is written to .research/history/REPO-candidates.json
with its outcome. The bug judgment and the fail-to-pass check happen afterwards,
in the same order. Needs network access and an authenticated gh CLI.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
from difflib import SequenceMatcher
import sys
import time

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
MAX_FILES = 10
MAX_LINES = 400
ISSUE_LIMIT = 4000
TYPESCRIPT_SOURCES = (".ts", ".tsx", ".js", ".jsx")
SOURCE_SUFFIXES = {"cobra": (".go",), "flask": (".py",), "fastify": (".js",), "gin": (".go",), "urfave-cli": (".go",),
                   "click": (".py",), "jinja": (".py",), "express": (".js",), "undici": (".js",),
                   "viper": (".go",), "chi": (".go",), "httpx": (".py",), "typer": (".py",), "koa": (".js",),
                   "axios": (".js",), "echo": (".go",), "resty": (".go",), "werkzeug": (".py",),
                   "marshmallow": (".py",), "hapi": (".js",), "mocha": (".js",),
                   "fiber": (".go",), "testify": (".go",), "starlette": (".py",), "black": (".py",),
                   "commander": (".js",), "ws": (".js",), "eslint": (".js",), "dayjs": (".js",),
                   **{repo: (".go",) for repo in ("zap", "validator", "logrus", "mux")},
                   **{repo: (".py",) for repo in ("requests", "tqdm", "httpie", "uvicorn", "pytest")},
                   **{repo: (".js",) for repo in ("handlebars", "winston", "pug", "superagent", "prettier")},
                   **{repo: TYPESCRIPT_SOURCES for repo in ("zod", "hono", "trpc", "tanstack-query", "vitest", "typeorm")},
                   # file_code_screen (v102): every source suffix graf indexes, per language.
                   "gorm": (".go",), "go-redis": (".go",), "aiohttp": (".py",), "poetry": (".py",),
                   **{repo: TYPESCRIPT_SOURCES for repo in ("lodash", "leaflet", "xstate", "zustand")},
                   **{repo: (".go",) for repo in ("websocket", "lo", "sqlx", "fasthttp")},  # natural_tasks_t9 (v105).
                   **{repo: (".py",) for repo in ("loguru", "pydantic", "drf", "sqlmodel")},
                   **{repo: (".js",) for repo in ("moment", "ramda", "jsdom", "nock", "async", "nodemon", "webpack")},
                   # natural_tasks_t10 (v113).
                   **{repo: (".go",) for repo in ("gjson", "bubbletea", "fzf", "task", "caddy", "lazygit", "badger")},
                   **{repo: (".py",) for repo in ("faker", "cookiecutter", "pre-commit", "fabric", "networkx", "tornado",
                                                  "locust", "pipenv")},
                   "stylelint": (".mjs",),
                   **{repo: (".js",) for repo in ("multer", "mongoose", "showdown", "underscore", "less", "jquery", "chartjs")},
                   # natural_tasks_t11 (v121).
                   **{repo: (".go",) for repo in ("excelize", "gh-cli", "chezmoi", "helm", "restic", "sops",
                                                  "golangci-lint", "hugo", "d2", "traefik")},
                   **{repo: (".py",) for repo in ("yt-dlp", "scrapy", "fastapi", "ansible", "mitmproxy", "searxng",
                                                  "aider", "thefuck")},
                   **{repo: (".js",) for repo in ("validatorjs", "node-red", "semantic-release", "highlightjs", "pm2")},
                   # natural_tasks_t12 (v128).
                   **{repo: (".go",) for repo in ("micro", "gitleaks", "lipgloss", "glance", "ntfy", "dive", "go-zero",
                                                  "beego", "prometheus", "syncthing")},
                   **{repo: (".py",) for repo in ("arrow", "boltons", "more-itertools", "attrs", "isort", "pendulum",
                                                  "hypothesis", "sympy", "textual", "markitdown")},
                   **{repo: (".js",) for repo in ("mathjs", "nunjucks", "prism", "ejs", "node-fetch", "chalk", "knex",
                                                  "browserify", "svelte")},
                   "cyberchef": (".mjs", ".js")}
T10_TESTS = {"pre-commit": ("tests/", "testing/"), "tornado": ("tornado/test/",), "locust": ("locust/test/",),
             "stylelint": ("system-tests/",), "less": ("packages/test-data/",),  # natural_tasks_t10 test_files (v113).
             **{repo: ("test/",) for repo in ("yt-dlp", "ansible", "mitmproxy")},  # natural_tasks_t11 (v121).
             "hypothesis": ("hypothesis/tests/",), "svelte": ("packages/svelte/tests/",),  # natural_tasks_t12 (v128).
             **{repo: ("tests/",) for repo in ("nunjucks", "prism", "cyberchef")}}
JS_RUNTIME = {"fastify": "fastify-full", "express": "express-full", "undici": "undici-full",
              "commander": "commander-full", "ws": "ws-full", "eslint": "eslint-full", "dayjs": "dayjs-full",
              **{repo: f"{repo}-full" for repo in ("handlebars", "winston", "pug", "superagent", "prettier",
                                                    "moment", "ramda", "nodemon", "webpack",
                                                    "stylelint", "multer", "mongoose", "less",
                                                    "validatorjs", "node-red", "highlightjs")}}
PYTHON_RUNTIME = {repo: f"{repo}-venv" for repo in ("starlette", "black",  # Batch t7 and t8: own virtualenvs.
                                                     "requests", "tqdm", "httpie", "uvicorn", "pytest",
                                                     "loguru", "pydantic", "drf",
                                                     "faker", "pre-commit", "networkx", "tornado", "locust", "pipenv",
                                                     "yt-dlp", "scrapy", "fastapi", "ansible", "mitmproxy", "searxng",
                                                     "thefuck")}
HIGHLIGHTJS_SUITES = ("api", "detect", "markup", "parser", "regex", "special")  # Node suites; builds/ and browser/ need a browser build.
PAGES = 40
CONTEXTS = (5, 3, 2, 1, 0)  # Context lines per changed region; the widest unique one wins.
QUERY = """query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: 100, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number title mergedAt mergeCommit { oid }
        closingIssuesReferences(first: 5) { nodes { number title body labels(first: 10) { nodes { name } } } } }
    }
  }
}"""


def classify(repo, path):
    """test, source, or other (docs, changelog, configuration)."""
    name = Path(path).name
    suffix = SOURCE_SUFFIXES[repo]
    if suffix == TYPESCRIPT_SOURCES and (set(Path(path).parts) & {"test", "tests", "__tests__"}
                                         or ".test." in name or ".spec." in name):
        return "test"
    if repo == "gorm" and path.startswith("tests/") or repo == "leaflet" and path.startswith("spec/"):
        return "test"
    if suffix == (".go",) and (name.endswith("_test.go") or "/testdata/" in f"/{path}"):
        return "test"
    if path.startswith(T10_TESTS.get(repo, ())) or repo in ("networkx", "sympy") and "tests" in Path(path).parts[:-1] \
            or repo == "markitdown" and len(Path(path).parts) > 3 and Path(path).parts[2] == "tests" \
            or repo == "stylelint" and "__tests__" in Path(path).parts[:-1] \
            or repo == "less" and len(Path(path).parts) > 3 and Path(path).parts[0] == "packages" and Path(path).parts[2] == "test":
        return "test"
    if suffix == (".py",) and path.startswith("tests/"):
        return "test"
    if repo in ("commander", "eslint", "nock") and path.startswith("tests/") or repo == "moment" and path.startswith("src/test/"):
        return "test"
    if (repo == "pytest" and path.startswith("testing/")) or (repo == "prettier" and path.startswith("tests/")):
        return "test"
    if repo == "handlebars" and path.startswith(("spec/", "tests/", "tasks/tests/")):
        return "test"
    if repo == "pug" and len(Path(path).parts) > 3 and Path(path).parts[0] == "packages" and Path(path).parts[2] == "test":
        return "test"
    if suffix == (".js",) and path.startswith("test/"):
        return "test"
    return "source" if path.endswith(SOURCE_SUFFIXES[repo]) else "other"


def revert_edits(path, pre_fix, post_fix, pinned):
    """Edits turning the pinned file back into its pre-fix form, or None if any change is not unique.

    Each changed region keeps as much surrounding context as still matches the pinned file
    exactly once, never more than the unchanged lines between it and its neighbours.
    """
    pre, post = pre_fix.splitlines(keepends=True), post_fix.splitlines(keepends=True)
    changes = [op for op in SequenceMatcher(None, pre, post, autojunk=False).get_opcodes() if op[0] != "equal"]
    edits, text = [], pinned
    for n, (_, i1, i2, j1, j2) in enumerate(changes):
        room_before = j1 - (changes[n - 1][4] if n else 0)
        room_after = (changes[n + 1][3] if n + 1 < len(changes) else len(post)) - j2
        for context in CONTEXTS:
            ahead, behind = min(context, room_before), min(context, room_after)
            before = "".join(post[j1 - ahead:j2 + behind])
            if before and text.count(before) == 1:
                after = "".join(post[j1 - ahead:j1] + pre[i1:i2] + post[j2:j2 + behind])
                edits.append({"path": path, "before": before, "after": after})
                text = text.replace(before, after, 1)
                break
        else:
            return None
    return edits


def clean_issue(title, body):
    text = re.sub(r"<!--.*?-->", "", (body or "").replace("\r\n", "\n"), flags=re.S).strip()
    text = f"{title}\n\n{text}"
    return text if len(text) <= ISSUE_LIMIT else text[:ISSUE_LIMIT] + "\n[truncated]"


def git(repo, *args, attempts=3):
    """Run git in the partial clone; retry, since blobs are fetched lazily over the network."""
    for attempt in range(attempts):
        run = subprocess.run(["git", "-C", str(HISTORY / repo), *args], capture_output=True, text=True)
        if run.returncode == 0:
            return run.stdout
        time.sleep(5 * (attempt + 1))
    raise subprocess.CalledProcessError(run.returncode, run.args, run.stdout, run.stderr)


def _graphql(command, attempts=4):
    for attempt in range(attempts):
        run = subprocess.run(command, capture_output=True, text=True)
        if run.returncode == 0:
            return json.loads(run.stdout)
        time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"GitHub GraphQL failed: {run.stderr.strip()}")


def pull_requests(slug, cache):
    """Merged pull requests, cached so the enumeration is reproducible."""
    if cache.is_file():
        return json.loads(cache.read_text())
    owner, name = slug.split("/")
    nodes, cursor = [], None
    for _ in range(PAGES):
        command = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
        if cursor:
            command += ["-F", f"cursor={cursor}"]
        page = _graphql(command)
        prs = page["data"]["repository"]["pullRequests"]
        nodes += prs["nodes"]
        if not prs["pageInfo"]["hasNextPage"]:
            break
        cursor = prs["pageInfo"]["endCursor"]
    cache.write_text(json.dumps(nodes) + "\n")
    return nodes


def candidate(repo, pr, pin):
    """Apply the automatic criteria; return the candidate record with its outcome."""
    issue = pr["closingIssuesReferences"]["nodes"][0]
    record = {"pr": pr["number"], "pr_title": pr["title"], "merged_at": pr["mergedAt"],
              "issue": issue["number"], "issue_title": issue["title"],
              "labels": [l["name"] for l in issue["labels"]["nodes"]]}
    commit = (pr.get("mergeCommit") or {}).get("oid")
    if not commit or subprocess.run(["git", "-C", str(HISTORY / repo), "merge-base", "--is-ancestor",
                                     commit, pin]).returncode != 0:
        return {**record, "outcome": "rejected", "reason": "merge commit is not an ancestor of the pin"}
    status = [line.split("\t") for line in git(repo, "diff", "--name-status", f"{commit}^1", commit).splitlines()]
    kinds = {path[-1]: classify(repo, path[-1]) for path in status}
    if any(s[0][0] not in "AM" for s in status):
        return {**record, "outcome": "rejected", "reason": "renames or deletions"}
    fixed = [p for p, k in kinds.items() if k != "test"]
    tests = [p for p, k in kinds.items() if k == "test"]
    if not any(kinds[p] == "source" for p in fixed) or not tests:
        return {**record, "outcome": "rejected", "reason": "needs source and test changes"}
    numstat = [line.split("\t") for line in git(repo, "diff", "--numstat", f"{commit}^1", commit).splitlines()]
    lines = sum(int(a) + int(d) for a, d, p in numstat if p in fixed and a != "-")
    if len(fixed) > MAX_FILES or lines > MAX_LINES:
        return {**record, "outcome": "rejected", "reason": f"fix too large ({len(fixed)} files, {lines} lines)"}
    faults, hidden = [], []
    for path, kind in kinds.items():
        added = next(s[0] for s in status if s[-1] == path) == "A"
        target = hidden if kind == "test" else faults
        if added:
            target.append({"path": path, "added": True})
            continue
        pinned = BASE / ".research/corpora" / repo / path
        try:
            edits = revert_edits(path, git(repo, "show", f"{commit}^1:{path}"), git(repo, "show", f"{commit}:{path}"),
                                 pinned.read_text()) if pinned.is_file() else None
        except UnicodeDecodeError:  # natural_tasks_t10 (v115): a binary file cannot be reverted as a text edit.
            return {**record, "outcome": "rejected", "reason": f"{path} is a binary file change"}
        if edits is None:
            return {**record, "outcome": "rejected", "reason": f"{path} does not revert uniquely on the pin"}
        target += edits
    body = issue["body"]
    return {**record, "outcome": "automatic criteria met", "commit": commit,
            "task": {"id": f"{repo}-{issue['number']}", "repo": repo, "issue": clean_issue(issue["title"], body),
                     "faults": faults, "hidden_tests": hidden, "verify": verify_command(repo, tests),
                     **({"runtime": JS_RUNTIME[repo]} if repo in JS_RUNTIME else {}),
                     **({"python_runtime": PYTHON_RUNTIME[repo]} if repo in PYTHON_RUNTIME else {})}}


WEBPACK_RUNNERS = (".test.js", ".basictest.js", ".unittest.js", ".longtest.js")


def verify_command(repo, tests):
    if repo == "express":
        return ["node_modules/.bin/mocha", "--require", "test/support/env", "--check-leaks",
                *sorted(t for t in tests if t.endswith(".js") and "/support/" not in t)]
    if repo in ("ws", "eslint"):
        return ["node_modules/.bin/mocha", *sorted(t for t in tests if t.endswith(".js"))]
    if repo in ("dayjs", "winston", "pug"):
        return ["node_modules/.bin/jest", *sorted(t for t in tests if t.endswith(".js"))]
    if repo == "prettier":  # Fixtures and snapshots run through their directory's runner; --ci never writes snapshots.
        runners = set()
        for test in tests:
            folder = Path(test).parent
            if folder.name == "__snapshots__":
                runners.add(str(folder.parent / Path(test).name.removesuffix(".snap")))
            elif test.endswith(".test.js"):
                runners.add(test)
            elif test.startswith("tests/format/"):
                runners.add(str(folder / "format.test.js"))
        return ["node_modules/.bin/jest", "--ci", *sorted(runners)]
    if repo == "handlebars":  # Its node project runs spec/*.js against lib/ directly, without a build.
        return ["node_modules/.bin/vitest", "run", "--project", "node",
                *sorted(t for t in tests if t.startswith("spec/") and t.endswith(".js"))]
    if repo == "moment":  # Its runner bundles src/ with rollup, then runs the named qunit files.
        names = sorted(t.removeprefix("src/test/").removesuffix(".js") for t in tests
                       if t.startswith(("src/test/moment/", "src/test/locale/")) and t.endswith(".js"))
        return ["node", "scripts/test.js", "--only=" + ",".join(names)]
    if repo == "ramda":  # Its spec script: mocha over source/ through @babel/register.
        return ["env", "BABEL_ENV=cjs", "node_modules/.bin/mocha", "--require", "@babel/register",
                *sorted(t for t in tests if t.endswith(".js") and "/helpers/" not in t and "/shared/" not in t)]
    if repo == "nodemon":  # Its spec script: mocha per test file with TEST=1.
        return ["env", "TEST=1", "node_modules/.bin/mocha", "--exit", "--timeout", "30000",
                *sorted(t for t in tests if t.endswith(".test.js"))]
    if repo == "webpack":  # Its test:base jest invocation on changed runnable test files; fixtures alone run nothing.
        return ["node", "--expose-gc", "--max-old-space-size=4096", "--experimental-vm-modules",
                "node_modules/jest-cli/bin/jest", *sorted(t for t in tests if t.startswith("test/") and "/" not in
                                                           t[len("test/"):] and t.endswith(WEBPACK_RUNNERS))]
    if repo == "stylelint":  # Its test script: node --test with its setup and reporter, on the changed test files.
        return ["node", "scripts/test.mjs", *sorted(t for t in tests if t.endswith((".mjs", ".js", ".cjs"))
                                                    and (".test." in Path(t).name or Path(t).name == "index.mjs"))]
    if repo == "multer":
        return ["node_modules/.bin/mocha", "--exit", "--check-leaks",
                *sorted(t for t in tests if t.startswith("test/") and t.endswith(".js") and t.count("/") == 1)]
    if repo == "mongoose":  # .mocharc.yml requires test/mocha-fixtures.js, which starts an in-memory MongoDB.
        return ["node_modules/.bin/mocha", "--exit", *sorted(t for t in tests if t.endswith(".test.js"))]
    if repo == "less":  # Its node suite runs every fixture in packages/test-data against lib/.
        return ["node", "packages/less/test/index.js"]
    if repo == "validatorjs":  # Its test:ci script without coverage: mocha through @babel/register over src/.
        return ["node_modules/.bin/mocha", "--require", "@babel/register", *sorted(t for t in tests if t.endswith(".js"))]
    if repo == "node-red":  # Its mocha script on the changed spec files.
        return ["node_modules/.bin/mocha", *sorted(t for t in tests if t.endswith("_spec.js"))]
    if repo == "highlightjs":  # Its build_and_test script: the node build, then mocha on the changed node suites.
        suites = sorted({"test/" + Path(t).parts[1] for t in tests
                         if len(Path(t).parts) > 2 and Path(t).parts[0] == "test" and Path(t).parts[1] in HIGHLIGHTJS_SUITES})
        return ["sh", "-c", "node tools/build.js -t node > /dev/null && node_modules/.bin/mocha " + " ".join(suites)] \
            if suites else None  # No node suite: no runtime, not checked.
    if SOURCE_SUFFIXES[repo] == (".go",):
        packages = sorted({"./" + str(Path(t).parent) for t in tests if t.endswith("_test.go")})
        return ["go", "test", *packages, "-count=1"]
    if repo == "black":  # Case files under tests/data/ are fixtures that tests/test_format.py runs.
        modules = {t for t in tests if t.startswith("tests/test_") and t.endswith(".py")}
        if any(t.startswith("tests/data/") for t in tests):
            modules.add("tests/test_format.py")
        return ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", *sorted(modules)]
    if repo == "faker":  # Its pytest plugin is registered by the installed package, which the workspace replaces.
        return ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "faker.contrib.pytest.plugin",
                *sorted(t for t in tests if t.endswith(".py"))]
    if repo == "tornado":  # Its runner silences the access log that its tests assert stays empty (v117).
        return ["{python}", "-m", "tornado.test.runtests",
                *sorted(t.removesuffix(".py").replace("/", ".") for t in tests if t.endswith("_test.py"))]
    if repo == "ansible":  # Unit tests, with lib/ on the path as ansible-test sets it; integration targets need ansible-test.
        units = sorted(t for t in tests if t.startswith("test/units/") and t.endswith(".py"))
        return ["env", "PYTHONPATH=lib", "{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", *units] if units else None
    if SOURCE_SUFFIXES[repo] == (".py",):
        return ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", *sorted(t for t in tests if t.endswith(".py"))]
    return ["node", "--test", *sorted(t for t in tests if t.endswith(".js"))]


def pinned(repo):
    """The pinned snapshot: the benchmark corpora, the batch t4/t7/t8/t9/t10 repositories, or the localization ones."""
    for r in json.loads((BASE / "benchmark/corpora.json").read_text())["repositories"]:
        if r["id"] == repo:
            return r
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    screen = prereg["localization_screen"]
    for spec in (prereg["agent_trials"]["natural_tasks_t4"], prereg["agent_trials"]["natural_tasks_t7"],
                 prereg["agent_trials"]["natural_tasks_t8"], prereg["agent_trials"]["natural_tasks_t9"],
                 prereg["agent_trials"]["natural_tasks_t10"], prereg["agent_trials"]["natural_tasks_t11"],
                 prereg["agent_trials"]["natural_tasks_t12"],
                 screen["confirmation"], screen["file_screen"],
                 screen["typescript"], screen["file_code_screen"]):
        if repo in spec["repositories"]:
            return {"id": repo, **spec["repositories"][repo]}
    raise KeyError(repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", choices=sorted(SOURCE_SUFFIXES))
    args = parser.parse_args()
    corpus = pinned(args.repo)
    slug = corpus["repository"].split("github.com/")[-1].removesuffix(".git")
    if not (HISTORY / args.repo).is_dir():
        HISTORY.mkdir(parents=True, exist_ok=True)
        # Flask's history holds an old zero-padded tree mode that strict fsck rejects; the partial
        # clone fetches blobs lazily later, so the setting must persist in the clone's config.
        subprocess.run(["git", "-c", "transfer.fsckObjects=false", "-c", "fetch.fsckObjects=false",
                        "clone", "--quiet", "--config", "transfer.fsckObjects=false",
                        "--config", "fetch.fsckObjects=false", "--filter=blob:none", f"https://github.com/{slug}.git",
                        str(HISTORY / args.repo)], check=True)
    pin = corpus["commit"]
    pin_date = git(args.repo, "show", "-s", "--format=%cI", pin).strip()
    prs = [pr for pr in pull_requests(slug, HISTORY / f"{args.repo}-prs.json")
           if pr["closingIssuesReferences"]["nodes"] and pr["mergedAt"] <= pin_date]
    prs.sort(key=lambda pr: pr["mergedAt"], reverse=True)
    records = [candidate(args.repo, pr, pin) for pr in prs]
    out = HISTORY / f"{args.repo}-candidates.json"
    out.write_text(json.dumps({"repo": args.repo, "pin": pin, "pin_date": pin_date, "candidates": records},
                              indent=1) + "\n")
    met = sum(r["outcome"] == "automatic criteria met" for r in records)
    print(f"{args.repo}: {len(records)} issue-closing PRs before the pin, {met} meet the automatic criteria -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
