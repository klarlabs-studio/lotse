"""The t14 screen (natural_tasks_t14.screen_v141): one builtin attempt per fail-to-pass candidate in the recorded fill
order, keeping a candidate whose attempt made 15 or more tool calls.

    python benchmark/t14_screen.py [--dry-run]

Languages take turns, one candidate each; within a language repositories follow the fill order, newest merge first.
A repository is passed over once 8 of its candidates are kept and a language once 16 are, so the screen attempts
every candidate the fill could still take and no other: the kept set the fill selects is the one a screen of all
candidates would give. Attempts, infrastructure reruns included, stop at the 150 agreed. Resumable: a candidate whose
trial summary exists is read, not rerun. Only tool-call counts are read; pass/fail and cost are not.
Writes benchmark/natural-tasks-t14-screen.json (the ordered candidates) and results/t14-screen.json.
"""
import argparse
import hashlib
import json
import subprocess
import sys

from agent_trials import BASE
from run_agent_batch import configure_solver, use_frozen_solver
from run_agent_trial import OrientationError, backend, preflight, run, solver
from t11_catalog import merge_dates
from t12_diagnostic import tool_calls

HISTORY = BASE / ".research/history"
SCREEN_CATALOG = BASE / "benchmark/natural-tasks-t14-screen.json"
OUT = BASE / "results/t14-screen.json"
TRIALS, ASIDE = BASE / ".research/trials", BASE / ".research/trials-aside"
INFRASTRUCTURE_RERUNS = 2


def passing(report, candidates):
    checked = {r["task"]: r["sanity_passed"] for r in json.loads((BASE / report).read_text())}
    return [{k: v for k, v in t.items() if k not in ("merged_at", "pr")}
            for t in json.loads((HISTORY / candidates).read_text()) if checked[t["id"]]]


def existing():
    """t12 fail-to-pass passes that neither t12 nor t13 used and that share no issue with their tasks."""
    used = [t for name in ("natural-tasks-t12.json", "natural-tasks-t13.json")
            for t in json.loads((BASE / "benchmark" / name).read_text())]
    ids, issues = {t["id"] for t in used}, {t["issue"] for t in used}
    return [t for t in passing("results/natural-tasks-t12-f2p.json", "t12-f2p-candidates.json")
            if t["id"] not in ids and t["issue"] not in issues]


def ordered(spec):
    """{language: [tasks of each repository in fill order, newest merge first]}."""
    pool = {}
    for task in existing() + passing("results/natural-tasks-t14-f2p.json", "t14-f2p-candidates.json"):
        pool.setdefault(task["repo"], []).append(task)
    order = {}
    for language, repos in spec["fill_order"].items():
        order[language] = []
        for repo in repos:
            dates = merge_dates(repo)
            order[language] += sorted(pool.pop(repo, []), key=lambda t: dates[t["id"]], reverse=True)
    if pool:
        raise SystemExit(f"Candidates outside the fill order: {sorted(pool)}")
    return order


def interleaved(order):
    """Languages take turns, one candidate each, in the order of spec['fill_order']."""
    queues = [list(tasks) for tasks in order.values()]
    while any(queues):
        for queue in queues:
            if queue:
                yield queue.pop(0)


def calls(identifier):
    return len(tool_calls(TRIALS / identifier / "agent-stream.jsonl")[0])


def attempt(task, identifier, used, budget):
    """One screen attempt, rerunning an infrastructure abort at most twice; returns the attempts it used."""
    for number in range(1, INFRASTRUCTURE_RERUNS + 2):
        if used >= budget:
            return used, False
        used += 1
        try:
            summary = run(task, identifier, "builtin", SCREEN_CATALOG)
        except OrientationError as error:
            summary = {"infrastructure_error": True, "orientation_error": str(error)}
        if not summary["infrastructure_error"]:
            if not summary["usage_complete"]:
                raise SystemExit(f"{identifier}: incomplete solver usage; artifacts preserved, screen stopped")
            return used, True
        target = ASIDE / f"{identifier}-infrastructure-{number}"
        ASIDE.mkdir(parents=True, exist_ok=True)
        (TRIALS / identifier).rename(target)
        (target / "aside.json").write_text(json.dumps({"trial": identifier, "reason": "infrastructure abort; rerun "
                                                       "from scratch under screen.infrastructure"}, indent=2) + "\n")
    raise SystemExit(f"{identifier}: infrastructure abort persisted after {INFRASTRUCTURE_RERUNS} reruns; screen stopped")


def frozen(spec):
    use_frozen_solver(spec)
    if subprocess.check_output([solver(), "--version"], text=True).strip() != spec["solver_version"]:
        raise SystemExit("Solver version differs from the screen's frozen version")
    if preflight("builtin", backend("builtin")) != spec["backend_sha256"]["builtin"]:
        raise SystemExit("builtin differs from the screen's frozen backend")
    configure_solver(spec)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())[
        "agent_trials"]["natural_tasks_t14"]["screen_v141"]
    order = ordered(spec)
    SCREEN_CATALOG.write_text(json.dumps([t for tasks in order.values() for t in tasks], indent=2) + "\n")
    if hashlib.sha256(SCREEN_CATALOG.read_bytes()).hexdigest() != spec["catalog_sha256"] and not args.dry_run:
        raise SystemExit("The screen catalog differs from the frozen one")
    language = {t["id"]: lang for lang, tasks in order.items() for t in tasks}
    by_repo, by_language, results = {}, {}, {}
    used = len(list(ASIDE.glob(f"{spec['trial_prefix']}-*")))  # Reruns already spent, on a resume.
    if not args.dry_run:
        frozen(spec)
    for task in interleaved(order):
        repo, lang = task["repo"], language[task["id"]]
        if by_repo.get(repo, 0) >= spec["per_repository"] or by_language.get(lang, 0) >= spec["per_language"]:
            continue
        identifier = f"{spec['trial_prefix']}-{task['id']}-builtin-1"
        if args.dry_run:
            print(identifier)
            continue
        if (TRIALS / identifier / "summary.json").is_file():
            used += 1
        elif (TRIALS / identifier).exists():
            raise SystemExit(f"{identifier} exists without a summary; inspect it before rerunning")
        else:
            used, ran = attempt(task, identifier, used, spec["attempt_budget"])
            if not ran:
                print("attempt budget reached", flush=True)
                break
        count = calls(identifier)
        kept = count >= spec["keep_at_tool_calls"]
        results[task["id"]] = {"repo": repo, "language": lang, "tool_calls": count, "kept": kept}
        by_repo[repo] = by_repo.get(repo, 0) + kept
        by_language[lang] = by_language.get(lang, 0) + kept
        print(json.dumps({"trial": identifier, "tool_calls": count, "kept": kept, "attempts": used}), flush=True)
    if not args.dry_run:
        OUT.write_text(json.dumps({"attempts": used, "kept_by_language": by_language, "candidates": results},
                                  indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
