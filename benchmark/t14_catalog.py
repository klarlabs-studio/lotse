"""Freeze the t14 catalog (natural_tasks_t14): the screen-kept candidates filled per language in fill_order, newest
merge first, at most 8 per repository and 16 per language (natural_tasks_t14.fill, screen_v141)."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "benchmark/natural-tasks-t14.json"


def main():
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["natural_tasks_t14"]
    screen = json.loads((BASE / "results/t14-screen.json").read_text())["candidates"]
    ordered = json.loads((BASE / spec["screen_v141"]["catalog"]).read_text())  # Fill order, newest merge first.
    language = {repo: lang for lang, repos in spec["screen_v141"]["fill_order"].items() for repo in repos}
    catalog, per_repo, per_language = [], {}, {}
    for task in ordered:
        repo, lang = task["repo"], language[task["repo"]]
        if screen.get(task["id"], {}).get("kept") and per_repo.get(repo, 0) < 8 and per_language.get(lang, 0) < 16:
            catalog.append(task)
            per_repo[repo], per_language[lang] = per_repo.get(repo, 0) + 1, per_language.get(lang, 0) + 1
    if len({t["issue"] for t in catalog}) != len(catalog):
        raise SystemExit("Two selected tasks share an issue")
    if len(catalog) < spec["minimum_tasks"]:
        raise SystemExit(f"Only {len(catalog)} tasks; natural_tasks_t14 requires {spec['minimum_tasks']}; nothing written")
    if len(catalog) != sum(v["kept"] for v in screen.values()):
        raise SystemExit("The fill dropped a kept task; the screen's stopping rule was not followed")
    OUT.write_text(json.dumps(catalog, indent=2) + "\n")
    print(len(catalog), "tasks", per_language)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
