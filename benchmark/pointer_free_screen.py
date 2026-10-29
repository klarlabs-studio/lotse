"""Feasibility screen for pointer-free natural tasks (preregistration v120, agent_trials.pointer_free_screen).

    python benchmark/pointer_free_screen.py

A task is pointer-free when its issue text names no faulted file (path, file name or stem) and shares no
code-shaped identifier with the faulted code. Counts every unused automatic-criteria candidate in
.research/history and writes results/pointer-free-screen.json. No solver, no test run.
"""
import ast
import collections
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = BASE / "results/pointer-free-screen.json"
DICTIONARY = Path("/usr/share/dict/words")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
KEYWORDS = {
    "Go": "break case chan const continue default defer else fallthrough for func go goto if import interface map "
          "package range return select struct switch type var",
    "Python": "False None True and as assert async await break class continue def del elif else except finally for "
              "from global if import in is lambda nonlocal not or pass raise return try while with yield",
    "JavaScript": "await break case catch class const continue debugger default delete do else export extends "
                  "finally for function if import in instanceof let new return super switch this throw try typeof "
                  "var void while with yield",
}
KEYWORDS["TypeScript"] = KEYWORDS["JavaScript"] + " enum implements interface keyof namespace private protected public readonly type"
RULE_LANGUAGES = ("Go", "Python", "JavaScript")  # v120 names these three; TypeScript counts only in the total.
SUFFIX_LANGUAGE = {".go": "Go", ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
                   ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript"}
MIN_CANDIDATES, MIN_REPOSITORIES = 90, 6


def code_identifiers(text, language, words):
    """Identifiers of 4+ characters shaped like code rather than prose."""
    keywords = set(KEYWORDS[language].split())
    found = set()
    for name in IDENTIFIER.findall(text):
        if len(name) < 4 or name in keywords:
            continue
        shaped = "_" in name or any(c.isdigit() for c in name) or any(c.isupper() for c in name[1:])
        prose = name.islower() and (len(name) < 6 or name.lower() in words)
        if shaped or (name.islower() and not prose):
            found.add(name)
    return found


def whole_word(word, text, flags=0):
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text, flags) is not None


def pointer_free(task, language, words):
    """The criterion's parts for one task, and whether none of them points at the faulted code."""
    issue = task["issue"]
    paths = sorted({f["path"] for f in task["faults"]})
    named_path = [p for p in paths if p in issue]
    named_file = [p for p in paths if Path(p).name.lower() in issue.lower()]
    stem = [p for p in paths if len(Path(p).stem) >= 4 and whole_word(Path(p).stem, issue, re.IGNORECASE)]
    names = set().union(*(code_identifiers(f.get("before", "") + "\n" + f.get("after", ""), language, words)
                          for f in task["faults"]))
    shared = sorted(n for n in names if whole_word(n, issue))
    return {"path": named_path, "file_name": named_file, "stem": stem, "identifiers": shared,
            "pointer_free": not (named_path or named_file or stem or shared)}


def used_ids():
    return {t["id"] for catalog in (BASE / "benchmark").glob("natural-tasks*.json") for t in json.loads(catalog.read_text())}


def t10_repositories():
    return set(json.loads((BASE / "benchmark/preregistration.json").read_text())
               ["agent_trials"]["natural_tasks_t10"]["repositories"])


def language_of(task):
    counts = collections.Counter(SUFFIX_LANGUAGE.get(Path(f["path"]).suffix) for f in task["faults"])
    counts.pop(None, None)
    return counts.most_common(1)[0][0] if counts else None


def main():
    words = {w.strip().lower() for w in DICTIONARY.read_text().splitlines()}
    used, fresh = used_ids(), t10_repositories()
    rows = []
    for path in sorted(HISTORY.glob("*-candidates.json")):
        if path.name.endswith("-without-snapshot.json"):
            continue
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or "candidates" not in record:
            continue
        for candidate in record["candidates"]:
            if candidate.get("outcome") != "automatic criteria met":
                continue
            task = candidate["task"] if isinstance(candidate["task"], dict) else ast.literal_eval(candidate["task"])
            language = language_of(task)
            if task["id"] in used or language is None:
                continue
            rows.append({"id": task["id"], "repo": task["repo"], "language": language,
                         "pool": "t10_repositories" if task["repo"] in fresh else "older_repositories",
                         **pointer_free(task, language, words)})
    summary = {}
    for pool in ("t10_repositories", "older_repositories", "all"):
        own = [r for r in rows if pool == "all" or r["pool"] == pool]
        free = [r for r in own if r["pointer_free"]]
        by_language = {lang: {"candidates": sum(r["language"] == lang for r in own),
                              "pointer_free": sum(r["language"] == lang for r in free),
                              "repositories": len({r["repo"] for r in free if r["language"] == lang})}
                       for lang in KEYWORDS}
        summary[pool] = {"candidates": len(own), "pointer_free": len(free), "by_language": by_language,
                         "by_repository": dict(sorted(collections.Counter(r["repo"] for r in free).items()))}
    every = summary["all"]
    passed = every["pointer_free"] >= MIN_CANDIDATES and all(
        every["by_language"][lang]["repositories"] >= MIN_REPOSITORIES for lang in RULE_LANGUAGES)
    OUT.write_text(json.dumps({"preregistration_version": 120, "passed": passed, "summary": summary, "rows": rows},
                              indent=1) + "\n")
    print(json.dumps({"passed": passed, **{k: {kk: v[kk] for kk in ("candidates", "pointer_free", "by_language")}
                                          for k, v in summary.items()}}, indent=1))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
