"""Offline fault-localization screen: does a retriever's first answer reach the code a real fix changed?

    python benchmark/localization.py build         # catalog from the mined issue-closing pull requests
    python benchmark/localization.py run [--split dev|heldout]
    python benchmark/localization.py decide

Follows localization_screen in benchmark/preregistration.json. Each task is a merged pull request
that closes an issue. The corpus is the snapshot just before the fix (the merge's first parent),
the query is the issue text, and the gold lines are the pre-fix source lines the fix changed.
Every arm answers once within the same payload budget, and the same rules score every arm.
"""
import argparse
import os
from collections import defaultdict
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import io

import tree_sitter as ts
import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_python

from retrieval import BASE, BM25, load_documents, tokens

WINDOW = 24
LANGUAGES = {".go": tree_sitter_go, ".py": tree_sitter_python, ".js": tree_sitter_javascript,
             ".mjs": tree_sitter_javascript, ".cjs": tree_sitter_javascript}
FUNCTIONS = {"function_declaration", "method_declaration", "function_definition", "decorated_definition",
             "generator_function_declaration", "method_definition", "function_expression", "arrow_function",
             "function"}
BINDINGS = {"variable_declarator", "lexical_declaration", "variable_declaration", "assignment_expression",
            "expression_statement", "pair", "export_statement"}
CALLS = {"call_expression": "function", "call": "function", "new_expression": "constructor"}
MAX_DEFINITIONS = 3  # A called name defined by more units than this is too ambiguous to link.
_PARSERS = {}


def gold_lines(pre_fix, post_fix):
    """Pre-fix line numbers a fix replaced or deleted; insertions mark the lines around them."""
    pre, post = pre_fix.splitlines(), post_fix.splitlines()
    lines = set()
    for op, i1, i2, _, _ in SequenceMatcher(None, pre, post, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            lines.update(range(i1 + 1, i2 + 1))
        elif op == "insert":
            lines.update(n for n in (i1, i1 + 1) if 1 <= n <= len(pre))
    return lines


def _parser(suffix):
    if suffix not in _PARSERS:
        _PARSERS[suffix] = ts.Parser(ts.Language(LANGUAGES[suffix].language()))
    return _PARSERS[suffix]


def _name(node):
    """The name a function is defined or bound under, or None."""
    field = node.child_by_field_name("name")
    if field is None and node.type == "decorated_definition":
        field = node.child_by_field_name("definition").child_by_field_name("name")
    if field is not None:
        return field.text.decode()
    parent = node.parent
    while parent is not None and parent.type in BINDINGS:
        for key in ("name", "left", "key"):
            target = parent.child_by_field_name(key)
            if target is not None:
                prop = target.child_by_field_name("property")
                return (prop or target).text.decode().strip("'\"")
        parent = parent.parent
    return None


def _span(node):
    """Extend a function to the statement that binds it, so its name is part of the unit."""
    top = node
    while top.parent is not None and top.parent.type in BINDINGS:
        top = top.parent
    return top.start_point[0] + 1, top.end_point[0] + 1


def _function_nodes(root):
    found, stack = [], [root]
    while stack:
        node = stack.pop()
        if node.type in FUNCTIONS and not (node.type == "function_definition" and node.parent is not None
                                           and node.parent.type == "decorated_definition"):
            found.append(node)
            continue  # Nested functions belong to the outermost one.
        stack.extend(reversed(node.children))
    return found


def _windows(path, lines, start, end, kind):
    return [{"path": path, "kind": kind, "name": None, "start": s, "end": min(s + WINDOW - 1, end),
             "text": "\n".join(lines[s - 1:min(s + WINDOW - 1, end)]), "calls": []}
            for s in range(start, end + 1, WINDOW)]


def _calls(node):
    names, stack = [], [node]
    while stack:
        current = stack.pop()
        if current.type in CALLS:
            target = current.child_by_field_name(CALLS[current.type])
            if target is not None:
                for key in ("field", "attribute", "property"):
                    if target.child_by_field_name(key) is not None:
                        target = target.child_by_field_name(key)
                        break
                names.append(target.text.decode())
        stack.extend(current.children)
    return names


def code_units(path, text):
    """Partition a file into function units plus module (or text) windows covering every other line."""
    lines = text.splitlines()
    suffix = Path(path).suffix
    if suffix not in LANGUAGES:
        return _windows(path, lines, 1, len(lines), "text")
    root = _parser(suffix).parse(text.encode()).root_node
    units, covered = [], 0
    for node in sorted(_function_nodes(root), key=lambda n: n.start_byte):
        start, end = _span(node)
        if start <= covered:
            continue  # Two definitions on one line: the first owns it.
        if start > covered + 1:
            units += _windows(path, lines, covered + 1, start - 1, "module")
        units.append({"path": path, "kind": "function", "name": _name(node), "start": start, "end": end,
                      "text": "\n".join(lines[start - 1:end]), "calls": _calls(node)})
        covered = end
    if covered < len(lines):
        units += _windows(path, lines, covered + 1, len(lines), "module")
    return units


def gold_units(units, lines):
    return [u for u in units if any(u["start"] <= n <= u["end"] for n in lines)]


def call_edges(units):
    """Directed caller -> callee edges between function units, linked by defined name."""
    defined = defaultdict(list)
    for i, unit in enumerate(units):
        if unit["kind"] == "function" and unit["name"]:
            defined[unit["name"]].append(i)
    edges = set()
    for i, unit in enumerate(units):
        for name in set(unit["calls"]):
            targets = defined.get(name, [])
            if len(targets) <= MAX_DEFINITIONS:
                edges.update((i, j) for j in targets if j != i)
    return sorted(edges)


def propagate(scores, edges, weight):
    """Add a share of the best neighbour's own score, treating calls as undirected."""
    best = [0.0] * len(scores)
    for a, b in edges:
        best[a] = max(best[a], scores[b])
        best[b] = max(best[b], scores[a])
    return [s + weight * n for s, n in zip(scores, best)]


def render_unit(unit):
    return f"{unit['path']}:{unit['start']}-{unit['end']}\n{unit['text']}\n\n"


def pack_units(units, scores, budget, floor):
    """Whole units in score order, stopping below floor * best score or when the next unit does not fit."""
    order = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], i))
    picked, text = [], ""
    for i in order:
        if scores[i] < floor * scores[order[0]]:
            break
        candidate = text + render_unit(units[i])
        if len(tokens(candidate)) > budget:
            if not picked:  # The best unit alone is too long: keep its first lines.
                text = _bounded(candidate, budget)
                end = units[i]["start"] + max(text.count("\n") - 2, 0)
                picked.append({**units[i], "end": min(end, units[i]["end"])})
            break
        picked.append(units[i])
        text = candidate
    return picked, text


def _bounded(text, budget):
    result = ""
    for line in text.splitlines(keepends=True):
        if len(tokens(result + line)) > budget:
            break
        result += line
    return result



def bounded_units(units, size=WINDOW):
    """Split function units longer than the window into window-sized pieces aligned to the function start."""
    result = []
    for unit in units:
        if unit["kind"] != "function" or unit["end"] - unit["start"] < size:
            result.append(unit)
            continue
        lines = unit["text"].split("\n")
        for offset in range(0, len(lines), size):
            start = unit["start"] + offset
            result.append({**unit, "start": start, "end": min(start + size - 1, unit["end"]),
                           "text": "\n".join(lines[offset:offset + size])})
    return result


def cited_units(spans, units_by_path):
    """Units reached by the cited spans, in citation order, each once."""
    seen, order = set(), []
    for path, start, end in spans:
        for unit in units_by_path.get(path, []):
            key = (path, unit["start"])
            if unit["start"] <= end and start <= unit["end"] and key not in seen:
                seen.add(key)
                order.append(unit)
    return order


def accuracy_at(ranked, gold, k):
    return any(item in gold for item in ranked[:k])


CODE_SPANS = re.compile(r"```.*?```|`[^`\n]+`", re.S)
CODE_TOKENS = re.compile(
    r"[\w./-]+\.(?:go|py|js|mjs|cjs|ts)\b"            # file paths
    r"|\b[A-Za-z_]\w+(?:\.[A-Za-z_]\w+)+\b"             # dotted names
    r"|\b[a-z]+[A-Z]\w*\b"                               # camelCase
    r"|\b[A-Z][a-z0-9]+[A-Z]\w*\b"                       # PascalCase with an inner capital
    r"|\b\w*[A-Za-z]_\w+\b"                                # snake_case and CONSTANTS
    r"|\b[A-Za-z_]\w*(?=\()")                            # called names


def rewrite_query(issue):
    """The issue title plus code-like tokens from the body (BLIZZARD-style); prose is dropped."""
    title, _, body = issue.partition("\n")
    found = CODE_TOKENS.findall(body)
    for span in CODE_SPANS.findall(body):
        found += re.findall(r"[A-Za-z_]\w+", span)
    tokens_ = list(dict.fromkeys(found))
    return f"{title}\n{' '.join(tokens_)}" if tokens_ else issue

GRAPHIFY_NODE = re.compile(r"^NODE (.*?) \[src=(.*?) loc=L?(\d*) community=", re.M)


def graphify_locations(text):
    """Cited files in order, and symbol locations; file nodes and external symbols locate nothing."""
    files, locations = [], []
    for label, src, line in GRAPHIFY_NODE.findall(text):
        if not src:
            continue
        if src not in files:
            files.append(src)
        if line and not (label == Path(src).name and line == "1"):
            locations.append((src, int(line)))
    return files, locations


def score_task(gold, units_by_path, files, spans, shown):
    """Shared hits: a gold file named, a gold unit reached by a cited span, and a gold line shown verbatim."""
    targets = [(p, u) for p, lines in gold.items() for u in gold_units(units_by_path.get(p, []), lines)]
    unit_hit = any(p == sp and u["start"] <= e and s <= u["end"] for p, u in targets for sp, s, e in spans)
    shown_hit = False
    for path, lines in gold.items():
        for unit in units_by_path.get(path, []):
            unit_lines = unit["text"].split("\n")
            for n in lines:
                if not unit["start"] <= n <= unit["end"]:
                    continue
                line = unit_lines[n - unit["start"]].strip() if n - unit["start"] < len(unit_lines) else ""
                covered = any(sp == path and s <= n <= e for sp, s, e in spans)
                if line and covered and line in shown:
                    shown_hit = True
    return {"file_hit": any(f in gold for f in files), "unit_hit": unit_hit, "gold_line_shown": shown_hit}


# --- Catalog -----------------------------------------------------------------------------------

HISTORY = BASE / ".research/history"
WORK = BASE / ".research/localization"
CATALOG = BASE / "benchmark/localization-tasks.json"
REPOS = ("cobra", "flask", "fastify")
REPLICATION_CATALOG = BASE / "benchmark/localization-replication-tasks.json"
REPLICATION_REPOS = ("gin", "urfave-cli", "click", "jinja", "express", "undici")
CONFIRMATION_CATALOG = BASE / "benchmark/localization-confirmation-tasks.json"
CONFIRMATION_REPOS = ("viper", "chi", "httpx", "typer", "koa", "axios")
# Split -> (catalog, repositories). dev is the 18-task part of the heldout catalog.
SPLITS = {"dev": (CATALOG, REPOS), "heldout": (CATALOG, REPOS),
          "replication": (REPLICATION_CATALOG, REPLICATION_REPOS),
          "confirmation": (CONFIRMATION_CATALOG, CONFIRMATION_REPOS)}


def catalog_for(split):
    """dev and heldout share the screen catalog; replication (v20) and confirmation (v21) have their own."""
    return SPLITS[split][0]
MAX_FILES, MAX_LINES = 10, 400


def _git(repo, *args, text=True):
    return subprocess.run(["git", "-C", str(HISTORY / repo), *args], capture_output=True, text=text,
                          check=True).stdout


def build_task(repo, record, issue, dev_ids, split="heldout"):
    """One catalog entry, or a rejection reason, for a mined pull request."""
    from mine_natural_tasks import classify, clean_issue
    commit = record.get("commit") or record.get("merge_commit")
    if not commit:
        return None, "no merge commit"
    try:
        status = [line.split("\t") for line in _git(repo, "diff", "--name-status", f"{commit}^1", commit).splitlines()]
        numstat = [line.split("\t") for line in _git(repo, "diff", "--numstat", f"{commit}^1", commit).splitlines()]
    except subprocess.CalledProcessError:
        return None, "commit or parent missing"
    fixed = [s[-1] for s in status if classify(repo, s[-1]) != "test"]
    modified = [s[-1] for s in status if s[0] == "M" and classify(repo, s[-1]) == "source"]
    lines = sum(int(a) + int(d) for a, d, *path in numstat if path[-1] in fixed and a != "-")
    if not modified:
        return None, "no modified non-test source file"
    if len(fixed) > MAX_FILES or lines > MAX_LINES:
        return None, f"fix too large ({len(fixed)} files, {lines} lines)"
    query = clean_issue(issue["title"], issue["body"])
    if not issue["body"] and not issue["title"].strip():
        return None, "empty issue"
    gold = {path: sorted(gold_lines(_git(repo, "show", f"{commit}^1:{path}"), _git(repo, "show", f"{commit}:{path}")))
            for path in modified}
    task_id = f"{repo}-{issue['number']}"
    return {"id": task_id, "repo": repo, "pr": record["pr"], "issue": issue["number"], "commit": commit,
            "labels": record.get("labels", []), "split": "dev" if task_id in dev_ids else split,
            "query": query, "gold": {p: g for p, g in gold.items() if g}}, None


def build(split="heldout"):
    split = "heldout" if split == "dev" else split
    dev_ids = {t["id"] for t in json.loads((BASE / "benchmark/natural-tasks.json").read_text())} if split == "heldout" else set()
    catalog, repos = SPLITS[split]
    tasks, rejected = [], []
    for repo in repos:
        prs = {pr["number"]: pr for pr in json.loads((HISTORY / f"{repo}-prs.json").read_text())}
        for record in json.loads((HISTORY / f"{repo}-candidates.json").read_text())["candidates"]:
            pr = prs[record["pr"]]
            issue = pr["closingIssuesReferences"]["nodes"][0]
            record = {**record, "merge_commit": (pr.get("mergeCommit") or {}).get("oid")}
            task, reason = build_task(repo, record, issue, dev_ids, split)
            if task and task["gold"] and not any(t["id"] == task["id"] for t in tasks):
                tasks.append(task)
            else:
                rejected.append({"repo": repo, "pr": record["pr"], "reason": reason or "duplicate issue or empty gold"})
    catalog.write_text(json.dumps({"tasks": tasks, "rejected": rejected}, indent=1) + "\n")
    count = defaultdict(int)
    for t in tasks:
        count[(t["repo"], t["split"])] += 1
    print(f"{len(tasks)} tasks ({dict(count)}), {len(rejected)} rejected -> {catalog.relative_to(BASE)}")


# --- Arms --------------------------------------------------------------------------------------

GRAF = Path(os.environ.get("GRAF_BINARY", BASE / "target/release/graf"))  # A pinned copy keeps long runs stable.
GRAF_FLAGS = {"graf": [], "graf_floor": ["--file-score", "damped", "--stop", "floor"],
              "graf_floor_ids": ["--file-score", "damped", "--stop", "floor"]}
GRAF_SPAN = re.compile(r'^"(.*?)":(\d+)-(\d+)$', re.M)
FLOOR, WEIGHT = 0.5, 0.5


def snapshot(task):
    """The pre-fix corpus, materialized once and filtered like load_documents."""
    root = WORK / "snapshots" / task["id"]
    if not (root / "input").is_dir():
        raw = WORK / "raw" / task["id"]
        shutil.rmtree(raw, ignore_errors=True)
        raw.mkdir(parents=True)
        archive = _git(task["repo"], "archive", f"{task['commit']}^1", text=False)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(raw, filter="data")
        files = load_documents(raw)
        shutil.rmtree(raw)
        for rel, text in files.items():
            (root / "input" / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / "input" / rel).write_text(text)
    return root, load_documents(root / "input")


def graf_locate_command(root, arm, query, budget):
    """Options first, then `--`, so an issue text that starts with a dash stays the query."""
    return [str(GRAF), "--root", str(root), "locate", "--budget", str(budget), "--limit", "1000", "--text",
            *GRAF_FLAGS[arm], "--", query]


def run_graf(root, arm, query, budget):
    if not (root / "input/.graf/index.json").is_file():
        subprocess.run([str(GRAF), "--root", str(root / "input"), "index", "--rebuild"], check=True,
                       capture_output=True)
    out = subprocess.run(graf_locate_command(root / "input", arm, query, budget), check=True,
                         capture_output=True, text=True).stdout
    spans = [(p, int(s), int(e)) for p, s, e in GRAF_SPAN.findall(out)]
    return {"payload": out, "files": list(dict.fromkeys(p for p, _, _ in spans)), "spans": spans}


def run_graphify(root, query, budget):
    from graphify.serve import _query_graph_text, _read_graph
    graph = root / "graphify/graphify-out/graph.json"
    if not graph.is_file():
        shutil.rmtree(root / "graphify", ignore_errors=True)
        subprocess.run([str(BASE / ".research/venv/bin/graphify"), "extract", str(root / "input"), "--code-only",
                        "--no-cluster", "--max-workers", "2", "--out", str(root / "graphify")], check=True,
                       capture_output=True, timeout=600, env={**__import__("os").environ, "PYTHONHASHSEED": "0"})
    out = _query_graph_text(_read_graph(str(graph)).to_undirected(), query, token_budget=budget)
    prefix = str(root / "input") + "/"
    out = out.replace(prefix, "")
    files, locations = graphify_locations(out)
    return {"payload": out, "files": files, "spans": [(p, n, n) for p, n in locations]}


def run_file_bm25(files, query, budget):
    paths = list(files)
    scores = BM25([files[p] for p in paths]).score(query)
    order = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], i))
    cited, text = [], ""
    for i in order:
        if scores[i] < FLOOR * scores[order[0]] or len(tokens(text + paths[i] + "\n")) > budget:
            break
        cited.append(paths[i])
        text += paths[i] + "\n"
    return {"payload": text, "files": cited, "spans": []}


def first_rank(ranked, gold):
    return next((i + 1 for i, item in enumerate(ranked) if item in gold), None)


def run_ast(units, edges, query, budget, graph):
    scores = list(BM25([u["text"] for u in units]).score(query))
    if graph:
        scores = propagate(scores, edges, WEIGHT)
    picked, text = pack_units(units, scores, budget, FLOOR)
    spans = [(u["path"], u["start"], u["end"]) for u in picked]
    return {"payload": text, "files": list(dict.fromkeys(p for p, _, _ in spans)), "spans": spans}


ARMS = ("graf", "graf_floor", "graf_floor_ids", "graphify", "file_bm25", "ast_units", "ast_graph", "ast_bounded",
        "ast_graph_ids")  # ast_graph_ids from v21; earlier splits were scored without it.
RANKS = (1, 5, 10)


def corpus_gold(gold, files):
    """Gold lines in files the corpus holds; gold in excluded paths (hidden, vendored) cannot be retrieved by any arm."""
    kept = {p: set(g) for p, g in gold.items() if p in files}
    return kept, sorted(p for p in gold if p not in files)


def run_task(task, budget):
    root, files = snapshot(task)
    units_by_path = {p: code_units(p, t) for p, t in files.items()}
    units = [u for p in sorted(units_by_path) for u in units_by_path[p]]
    edges = call_edges(units)
    gold, dropped = corpus_gold(task["gold"], files)
    if not gold:
        return {"excluded": f"every gold file is outside the corpus: {dropped}"}
    gold_keys = {(p, u["start"]) for p, lines in gold.items() for u in gold_units(units_by_path[p], lines)}
    pieces = bounded_units(units)
    results = {}
    for arm in ARMS:
        if arm in GRAF_FLAGS:
            query = rewrite_query(task["query"]) if arm.endswith("_ids") else task["query"]
            answer = run_graf(root, arm, query, budget)
        elif arm == "graphify":
            answer = run_graphify(root, task["query"], budget)
        elif arm == "file_bm25":
            answer = run_file_bm25(files, task["query"], budget)
        elif arm == "ast_bounded":
            answer = run_ast(pieces, [], task["query"], budget, graph=False)
        elif arm == "ast_graph_ids":
            answer = run_ast(units, edges, rewrite_query(task["query"]), budget, graph=True)
        else:
            answer = run_ast(units, edges, task["query"], budget, graph=arm == "ast_graph")
        ranked = [(u["path"], u["start"]) for u in cited_units(answer["spans"], units_by_path)]
        results[arm] = {**score_task(gold, units_by_path, answer["files"], answer["spans"], answer["payload"]),
                        "unit_rank": first_rank(ranked, gold_keys), "file_rank": first_rank(answer["files"], gold),
                        "cited_units": len(ranked), "payload_tokens": len(tokens(answer["payload"])),
                        "files": answer["files"][:20], "spans": answer["spans"][:40]}
    return {"dropped_gold_files": dropped, "arms": results}


def write_json_atomic(path, data):
    """Write beside the target and rename, so a full disk never truncates earlier results."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=1) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run(split):
    import hashlib
    catalog = json.loads(catalog_for(split).read_text())
    digest = hashlib.sha256(catalog_for(split).read_bytes()).hexdigest()
    out = BASE / f"results/localization-{split}.json"
    previous = json.loads(out.read_text()) if out.is_file() else {}
    rows = {r["task"]: r for r in previous.get("tasks", []) if previous.get("catalog_sha256") == digest}
    budget = json.loads((BASE / "benchmark/preregistration.json").read_text())["localization_screen"]["budget_tokens"]
    graf_sha = hashlib.sha256(GRAF.read_bytes()).hexdigest()
    for task in [t for t in catalog["tasks"] if t["split"] == split]:
        if task["id"] in rows:
            continue
        rows[task["id"]] = {"task": task["id"], "repo": task["repo"], **run_task(task, budget)}
        print(task["id"], {a: r["unit_rank"] for a, r in rows[task["id"]].get("arms", {}).items()}
              or rows[task["id"]]["excluded"], flush=True)
        write_json_atomic(out, {"split": split, "catalog_sha256": digest, "graf_sha256": graf_sha,
                                "tasks": list(rows.values())})
        shutil.rmtree(WORK / "snapshots" / task["id"], ignore_errors=True)  # Regenerable from git; disk is finite.


# --- Decision ----------------------------------------------------------------------------------

def mcnemar_one_sided(arm, comparator):
    """Exact one-sided McNemar: P(at least this many arm-only wins | discordant tasks split 50/50)."""
    from math import comb
    wins = sum(1 for a, c in zip(arm, comparator) if a and not c)
    losses = sum(1 for a, c in zip(arm, comparator) if c and not a)
    n = wins + losses
    return sum(comb(n, k) for k in range(wins, n + 1)) / 2 ** n if n else 1.0


def hit_at(result, k):
    return result["unit_rank"] is not None and result["unit_rank"] <= k


def decide(rows, structured, comparators, alpha, k=5):
    hit = {a: [hit_at(r["arms"][a], k) for r in rows] for a in {*structured, *comparators}}
    arms = {}
    for arm in structured:
        versus = {c: {"p": mcnemar_one_sided(hit[arm], hit[c]),
                      "arm_only": sum(a and not b for a, b in zip(hit[arm], hit[c])),
                      "comparator_only": sum(b and not a for a, b in zip(hit[arm], hit[c]))} for c in comparators}
        arms[arm] = {"rate": sum(hit[arm]) / len(rows), "versus": versus, "p": max(v["p"] for v in versus.values())}
    # Holm step-down across structured arms on their intersection-union p values.
    rejecting = True
    for rank, arm in enumerate(sorted(arms, key=lambda a: arms[a]["p"])):
        arms[arm]["holm_alpha"] = alpha / (len(arms) - rank)
        rejecting = rejecting and arms[arm]["p"] <= arms[arm]["holm_alpha"]
        arms[arm]["qualifies"] = rejecting
    return {"tasks": len(rows), "comparator_rates": {c: sum(hit[c]) / len(rows) for c in comparators},
            "arms": arms, "qualified": [a for a in structured if arms[a]["qualifies"]]}


def summarize(rows):
    """Every arm's rates and payloads, overall and by repository."""
    def rates(subset):
        out = {}
        for arm in [a for a in ARMS if a in subset[0]["arms"]]:
            results = [r["arms"][arm] for r in subset]
            out[arm] = {key: sum(bool(x[key]) for x in results) / len(results)
                        for key in ("unit_hit", "file_hit", "gold_line_shown")}
            for k in RANKS:
                out[arm][f"unit_acc@{k}"] = sum(hit_at(x, k) for x in results) / len(results)
                out[arm][f"file_acc@{k}"] = sum(x["file_rank"] is not None and x["file_rank"] <= k
                                                for x in results) / len(results)
            out[arm]["mean_payload_tokens"] = sum(x["payload_tokens"] for x in results) / len(results)
        return out
    repos = sorted({r["repo"] for r in rows})
    return {"all": rates(rows), **{repo: rates([r for r in rows if r["repo"] == repo]) for repo in repos},
            "counts": {repo: sum(r["repo"] == repo for r in rows) for repo in repos}}


def decide_split(split):
    screen = json.loads((BASE / "benchmark/preregistration.json").read_text())["localization_screen"]
    data = json.loads((BASE / f"results/localization-{split}.json").read_text())
    rule = screen[split]["decision"] if split in ("replication", "confirmation") else screen["decision"]
    planned = {t["id"] for t in json.loads(catalog_for(split).read_text())["tasks"] if t["split"] == split}
    if {r["task"] for r in data["tasks"]} != planned:
        raise SystemExit(f"{split}: {len(data['tasks'])} of {len(planned)} tasks run; no decision on a partial split")
    excluded = {r["task"]: r["excluded"] for r in data["tasks"] if "excluded" in r}
    data["tasks"] = [r for r in data["tasks"] if "excluded" not in r]
    scored = [a for a in ARMS if a in data["tasks"][0]["arms"] and a != "file_bm25"]
    result = {"excluded_tasks": excluded,
              "dropped_gold_files": {r["task"]: r["dropped_gold_files"] for r in data["tasks"] if r.get("dropped_gold_files")},"split": split, "catalog_sha256": data["catalog_sha256"], "graf_sha256": data["graf_sha256"],
              "decision": decide(data["tasks"], rule["candidate_arms"], rule["comparators"], 0.05, rule["k"]),
              "summary": summarize(data["tasks"]),
              "pairwise_unit_acc@k": {f"{a}>{b}": mcnemar_one_sided([hit_at(r["arms"][a], rule["k"]) for r in data["tasks"]],
                                                                    [hit_at(r["arms"][b], rule["k"]) for r in data["tasks"]])
                                    for a in scored for b in scored if a != b}}
    out = BASE / f"results/localization-decision-{split}.json"
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({"qualified": result["decision"]["qualified"],
                      "unit_acc@k": {a: round(v[f"unit_acc@{rule['k']}"], 3) for a, v in result["summary"]["all"].items()}}))
    return 0 if result["decision"]["qualified"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "run", "decide"])
    parser.add_argument("--split", choices=list(SPLITS), default="dev")
    args = parser.parse_args()
    if args.command == "build":
        build(args.split)
        return 0
    if args.command == "run":
        run(args.split)
        return 0
    return decide_split(args.split)


if __name__ == "__main__":
    sys.exit(main())
