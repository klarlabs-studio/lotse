"""Freeze the t15 catalog (natural_tasks_t15): as benchmark/t14_catalog.py, the screen-kept candidates filled per
language in fill_order, newest merge first, at most 8 per repository and 16 per language (natural_tasks_t15.fill,
screen_v148)."""
import t14_catalog
from t14_catalog import BASE

OUT = BASE / "benchmark/natural-tasks-t15.json"

if __name__ == "__main__":
    raise SystemExit(t14_catalog.main("t15", "screen_v148", OUT))
