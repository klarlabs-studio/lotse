"""Freeze the t15 catalog (natural_tasks_t15): as benchmark/t14_catalog.py, the screen-kept candidates filled per
language in fill_order, newest merge first, at most 8 per repository and 16 per language (natural_tasks_t15.fill,
screen_v148); then, under catalog_v150, without the repositories Graphify cannot index within the setup limit."""
import json

import t14_catalog
from t14_catalog import BASE

OUT = BASE / "benchmark/natural-tasks-t15.json"
NO_GRAPHIFY_SETUP = ("pdfkit",)  # `graphify update .` stalls at 200 of 219 files (catalog_v150).


def main():
    t14_catalog.main("t15", "screen_v148", OUT)
    catalog = [t for t in json.loads(OUT.read_text()) if t["repo"] not in NO_GRAPHIFY_SETUP]
    OUT.write_text(json.dumps(catalog, indent=2) + "\n")
    print(len(catalog), "tasks after catalog_v150")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
